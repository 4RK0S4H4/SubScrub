// The C# engine checked against the retired Python engine's answers (tests/golden). The golden mode runs the other two.
//   parity [cleaner] <cases.json> <out.json>     the cleaner and the subtitle formats
//   parity session <cases.json> <out.json>       the file list, Review and the save questions
//   parity golden <tests/golden> <repository> [--update]   against the frozen answers of the retired Python engine
if (args.Length >= 3 && args[0] == "golden")
    return GoldenParity.Run(args[1], args[2], args.Contains("--update"));
if (args.Length == 3 && args[0] == "session")
    SessionParity.Run(args[1], args[2]);
else if (args.Length == 3 && args[0] == "cleaner")
    CleanerParity.Run(args[1], args[2]);
else
    CleanerParity.Run(args[0], args[1]);
return 0;
