using System.Collections.Concurrent;
using System.Diagnostics;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text;

// ── Step 0: AssemblyResolve hook — MUST come before any Libation type is touched ─
// The JIT resolves types when it compiles a method. By keeping this top-level code
// free of any Libation references, and delegating everything else to
// LibationBridgeApp.RunAsync (NoInlining), the hook is in place before the JIT
// ever tries to load AppScaffolding.dll / FileLiberator.dll / etc.
AppDomain.CurrentDomain.AssemblyResolve += (_, args) =>
{
    var name = new AssemblyName(args.Name).Name + ".dll";
    var path = Path.Combine("/usr/lib/libation", name);
    return File.Exists(path) ? Assembly.LoadFrom(path) : null;
};

await LibationBridgeApp.RunAsync(args);

// ── All Libation types are isolated here so the JIT resolves them lazily ──────────
static class LibationBridgeApp
{
    // NoInlining prevents the JIT from pulling Libation type references back
    // into Main before the AssemblyResolve hook above has a chance to fire.
    [MethodImpl(MethodImplOptions.NoInlining)]
    public static async Task RunAsync(string[] args)
    {
        // ── Step 1: Initialize Libation scaffolding ────────────────────────────
        // Force Libation to use /config as its files directory.
        // Libation's path discovery falls back to CWD when Assembly.GetEntryAssembly().Location
        // is empty (as it is for single-file published apps). Setting CWD to /config ensures
        // it finds Settings.json there and uses /config as its files directory.
        Environment.SetEnvironmentVariable("LIBATION_FILES", "/config");
        if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("HOME")))
            Environment.SetEnvironmentVariable("HOME", "/home/libation");
        Directory.SetCurrentDirectory("/config");

        var config = AppScaffolding.LibationScaffolding.RunPreConfigMigrations();
        AppScaffolding.LibationScaffolding.RunPostConfigMigrations(config, false);
        AppScaffolding.LibationScaffolding.RunPostMigrationScaffolding(AppScaffolding.Variety.Chardonnay, config);

        // ── Step 2: In-memory state ────────────────────────────────────────────
        var progress = new ConcurrentDictionary<string, ProgressInfo>();
        var downloadAllRunning = 0; // 0 = idle, 1 = running (Interlocked)

        // Background: remove completed progress entries after 1 hour
        _ = Task.Run(async () =>
        {
            while (true)
            {
                await Task.Delay(TimeSpan.FromMinutes(15));
                var cutoff = DateTimeOffset.UtcNow.AddHours(-1);
                foreach (var kv in progress.ToArray())
                    if (kv.Value.CompletedAt.HasValue && kv.Value.CompletedAt < cutoff)
                        progress.TryRemove(kv.Key, out _);
            }
        });

        // ── Step 3: Build the minimal API ─────────────────────────────────────
        var builder = WebApplication.CreateBuilder(args);
        builder.WebHost.UseUrls("http://localhost:8001");
        // Scan can take up to 10 minutes — raise Kestrel keep-alive limits
        builder.WebHost.ConfigureKestrel(o =>
        {
            o.Limits.KeepAliveTimeout = TimeSpan.FromMinutes(12);
            o.Limits.RequestHeadersTimeout = TimeSpan.FromMinutes(12);
        });
        var app = builder.Build();

        // GET /health
        app.MapGet("/health", () => Results.Ok(new { status = "ok" }));

        // GET /debug — shows DB path and book count for diagnostics
        app.MapGet("/debug", () =>
        {
            try
            {
                var dbPath = LibationFileManager.SqliteStorage.DatabasePath;
                var all = ApplicationServices.DbContexts.GetLibrary_Flat_NoTracking(false);
                var sample = all.Take(3).Select(lb => lb.Book?.AudibleProductId).ToList();
                return Results.Ok(new { db_path = dbPath, book_count = all.Count, sample });
            }
            catch (Exception ex)
            {
                return Results.Ok(new { error = ex.ToString() });
            }
        });

        // GET /accounts — shim over libationcli list-accounts --bare
        app.MapGet("/accounts", async () =>
        {
            using var proc = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = "/usr/bin/libationcli",
                    Arguments = "list-accounts --bare --libationFiles /config",
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                }
            };
            proc.StartInfo.EnvironmentVariables["HOME"] = "/home/libation";
            proc.Start();

            var stdoutTask = proc.StandardOutput.ReadToEndAsync();
            await proc.WaitForExitAsync();
            var stdout = await stdoutTask;

            var accounts = new List<object>();
            foreach (var line in stdout.Split('\n', StringSplitOptions.RemoveEmptyEntries))
            {
                var parts = line.Split('\t');
                if (parts.Length < 5) continue;
                accounts.Add(new
                {
                    account_id    = parts[0].Trim(),
                    name          = parts[1].Trim(),
                    locale        = parts[2].Trim(),
                    scan_library  = parts[3].Trim().Equals("yes",  StringComparison.OrdinalIgnoreCase)
                                 || parts[3].Trim().Equals("true", StringComparison.OrdinalIgnoreCase),
                    authenticated = parts[4].Trim().Equals("yes",  StringComparison.OrdinalIgnoreCase)
                                 || parts[4].Trim().Equals("true", StringComparison.OrdinalIgnoreCase),
                });
            }
            return Results.Ok(accounts);
        });

        // POST /scan — synchronous: holds connection open until libationcli scan exits
        app.MapPost("/scan", async () =>
        {
            var lockPath = "/config/SearchEngine/write.lock";
            if (File.Exists(lockPath))
                try { File.Delete(lockPath); } catch { /* ignore */ }

            using var proc = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = "/usr/bin/libationcli",
                    Arguments = "scan --libationFiles /config",
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                }
            };
            proc.StartInfo.EnvironmentVariables["HOME"] = "/home/libation";
            proc.Start();

            var stdoutTask = proc.StandardOutput.ReadToEndAsync();
            var stderrTask = proc.StandardError.ReadToEndAsync();
            await proc.WaitForExitAsync();
            var stdout = await stdoutTask;
            var stderr = await stderrTask;
            var output = stdout + (stderr.Length > 0 ? "\n" + stderr : "");

            return Results.Ok(new { exit_code = proc.ExitCode, output });
        });

        // POST /download/{asin} — starts DownloadDecryptBook.ProcessAsync in background
        app.MapPost("/download/{asin}", (string asin) =>
        {
            if (progress.TryGetValue(asin, out var existing) && existing.Status == "running")
                return Results.Conflict(new { error = "already in progress" });

            progress[asin] = new ProgressInfo { Status = "running", Progress = 0 };

            _ = Task.Run(async () =>
            {
                try
                {
                    var allBooks = ApplicationServices.DbContexts.GetLibrary_Flat_NoTracking(false);
                    var book = allBooks.FirstOrDefault(lb =>
                        string.Equals(lb.Book?.AudibleProductId, asin, StringComparison.OrdinalIgnoreCase));
                    if (book is null)
                    {
                        progress[asin] = new ProgressInfo
                        {
                            Status = "error",
                            Output = $"Book {asin} not found in library — run a scan first.",
                            CompletedAt = DateTimeOffset.UtcNow,
                        };
                        return;
                    }

                    var processor = FileLiberator.DownloadDecryptBook.Create(config);
                    processor.StreamingProgressChanged += (_, e) =>
                    {
                        if (progress.TryGetValue(asin, out var info))
                            info.Progress = (int)(e.ProgressPercentage ?? 0);
                    };

                    await processor.ProcessAsync(book);

                    progress[asin] = new ProgressInfo
                    {
                        Status = "complete",
                        Progress = 100,
                        CompletedAt = DateTimeOffset.UtcNow,
                    };
                }
                catch (Exception ex)
                {
                    progress[asin] = new ProgressInfo
                    {
                        Status = "error",
                        Output = ex.ToString(),
                        CompletedAt = DateTimeOffset.UtcNow,
                    };
                }
            });

            return Results.Accepted($"/progress/{asin}", new { asin, status = "started" });
        });

        // GET /progress/{asin}
        app.MapGet("/progress/{asin}", (string asin) =>
            progress.TryGetValue(asin, out var info)
                ? Results.Ok(new { asin, progress = info.Progress, status = info.Status, output = info.Output ?? "" })
                : Results.NotFound(new { error = "not found" }));

        // POST /download-all — fires libationcli liberate with no args, returns 202
        app.MapPost("/download-all", async () =>
        {
            if (Interlocked.CompareExchange(ref downloadAllRunning, 1, 0) != 0)
                return Results.Conflict(new { error = "already in progress" });

            _ = Task.Run(async () =>
            {
                try
                {
                    using var proc = new Process
                    {
                        StartInfo = new ProcessStartInfo
                        {
                            FileName = "/usr/bin/libationcli",
                            Arguments = "liberate --force --libationFiles /config",
                            RedirectStandardOutput = true,
                            RedirectStandardError = true,
                            UseShellExecute = false,
                        }
                    };
                    proc.StartInfo.EnvironmentVariables["HOME"] = "/home/libation";
                    proc.Start();
                    await proc.WaitForExitAsync();
                }
                finally
                {
                    Interlocked.Exchange(ref downloadAllRunning, 0);
                }
            });

            return Results.Accepted("/download-all", new { status = "started" });
        });

        app.Run();
    }
}

// ── Supporting types ──────────────────────────────────────────────────────────────
class ProgressInfo
{
    public string Status { get; set; } = "running";
    public int Progress { get; set; } = 0;
    public string? Output { get; set; }
    public DateTimeOffset? CompletedAt { get; set; }
}
