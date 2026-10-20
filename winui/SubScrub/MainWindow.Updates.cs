using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using SubScrub.Models;
using SubScrub.Services;

namespace SubScrub;

/// <summary>Updates (the Qt app's way): a quiet banner when GitHub has a newer release, at most once a day a few
/// seconds after start (Settings can switch it off), and on demand from ⋯ → Check for Updates. Update downloads the
/// installer, checks it, runs it quietly and closes SubScrub; the installer starts the new version.</summary>
public sealed partial class MainWindow
{
    private bool _updating;

    private void StartUpdateCheck()
    {
        if (Shots.Dir != null)
            return;
        UpdateBanner.ActionClicked += () => _ = InstallUpdateAsync();
        _ = AutoCheckAsync();
    }

    private async Task AutoCheckAsync()
    {
        await Task.Delay(5000);
        try
        {
            ShowUpdate(await Core!.CallRaw("update_check", new { manual = false }) as JsonObject);
        }
        catch (CoreException e)
        {
            Log.Write($"update check: {e.Message}");
        }
    }

    private void ShowUpdate(JsonObject? r)
    {
        if (r?["version"] is not JsonValue v)
            return;
        UpdateBanner.Text = $"SubScrub {v.GetValue<string>()} is available.";
        UpdateBanner.ActionText = r["button"]?.GetValue<string>() ?? "Update";
        UpdateBar.Visibility = Visibility.Visible;
    }

    private void OnUpdateLater(object sender, RoutedEventArgs e)
    {
        UpdateBar.Visibility = Visibility.Collapsed;
        _ = Safe(() => Core!.Do("update_later"));
    }

    private async void OnCheckUpdatesClick(object sender, RoutedEventArgs e)
    {
        if (Core == null)
            return;
        JsonObject? r;
        try
        {
            r = await Core.CallRaw("update_check", new { manual = true }) as JsonObject;
        }
        catch (CoreException ex)
        {
            await TellAsync(new Message { Title = "Couldn't check for updates", Text = ex.Message });
            return;
        }
        var current = r?["current"]?.GetValue<string>() ?? "";
        if (r?["error"] is JsonValue err)
        {
            await TellAsync(new Message { Title = "Couldn't check for updates", Text = err.GetValue<string>() });
            return;
        }
        if (r?["version"] is not JsonValue v)
        {
            await TellAsync(new Message { Title = "You're up to date", Text = $"SubScrub {current} is the newest version." });
            return;
        }
        ShowUpdate(r);
        var install = r["can_install"]?.GetValue<bool>() == true;
        var (choice, _) = await AskAsync(new Question
        {
            Key = "update", Title = $"SubScrub {v.GetValue<string>()} is available",
            Text = install ? $"You have {current}. Updating takes a moment and SubScrub restarts by itself."
                : $"You have {current}. The new version is on the download page.",
            Buttons = [new() { Key = "later", Label = "Later" },
                       new() { Key = "go", Label = install ? "Update" : "Open Download Page", Kind = "primary" }],
        });
        if (choice == "go")
            await InstallUpdateAsync();
    }

    private async Task InstallUpdateAsync()
    {
        if (Core == null || _updating)
            return;
        if (Files.Busy)
        {
            await TellAsync(new Message { Title = "Saving is still running", Text = "Update once the files have finished saving." });
            return;
        }
        var pending = Files.Rows.Count(r => r.State is "queued" or "probing" or "reading" or "ready" or "failed");
        if (pending > 0 && UpdateBanner.ActionText == "Update")
        {
            var (choice, _) = await AskAsync(new Question
            {
                Key = "update", Title = "Update now?",
                Text = $"Updating restarts SubScrub, and the {pending} file{(pending == 1 ? "" : "s")} in the list will be cleared. "
                       + "Nothing has been changed in them.",
                Buttons = [new() { Key = "cancel", Label = "Not Now" }, new() { Key = "go", Label = "Update and Restart", Kind = "primary" }],
            });
            if (choice != "go")
                return;
        }
        _updating = true;
        try
        {
            if (UpdateBanner.ActionText == "Update")
                StatusText.Text = "Downloading the update…";
            var r = await Core.CallRaw("update_install") as JsonObject;
            if (r?["installing"] is not null)
            {
                StatusText.Text = "Installing the update. SubScrub restarts by itself.";
                _closing = true;
                Core.Dispose();
                Close();
            }
        }
        catch (CoreException ex)
        {
            StatusText.Text = "";
            await TellAsync(new Message { Title = "Couldn't update", Text = ex.Message });
        }
        finally
        {
            _updating = false;
        }
    }

    private void OnUpdateProgress(double fraction)
    {
        if (_updating)
            StatusText.Text = $"Downloading the update… {(int)(fraction * 100)}%";
    }
}
