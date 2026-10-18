namespace SubScrub.Models;

/// <summary>How a file stands: the colour of its left-edge bar and its one badge.</summary>
public enum FileState { Ok, Warn, Bad, Mute }

/// <summary>One video or subtitle file in the Files list (Stage 1: sample data only, nothing is read or written).</summary>
public sealed record FileItem(
    string Name,
    string? NewName,
    string? Warning,
    string Tracks,
    int ToClean,
    FileState State,
    string Badge);
