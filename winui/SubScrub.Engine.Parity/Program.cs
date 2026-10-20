// The C# engine's side of the parity checks (tools/cleaner_parity.py, tools/engine_parity.py).
//   parity [cleaner] <cases.json> <out.json>     the cleaner and the subtitle formats
//   parity session <cases.json> <out.json>       the file list, Review and the save questions
if (args.Length == 3 && args[0] == "session")
    SessionParity.Run(args[1], args[2]);
else if (args.Length == 3 && args[0] == "cleaner")
    CleanerParity.Run(args[1], args[2]);
else
    CleanerParity.Run(args[0], args[1]);
