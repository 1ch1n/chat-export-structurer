"""Unified CLI for MyChatArchive.

Commands:
    mychatarchive init                Interactive setup (configure backends + sources)
    mychatarchive sync                One-command import from all sources
    mychatarchive import <file>       Import a chat export
    mychatarchive export <output>     Export archive to JSON/CSV/SQLite
    mychatarchive embed               Generate vector embeddings
    mychatarchive summarize           Generate LLM summaries for all threads
    mychatarchive groups              Manage thread groups (list/create/add/remove)
    mychatarchive classify            Set sensitivity levels (public/private/sealed)
    mychatarchive serve               Start MCP server
    mychatarchive search <query>      Search from the command line
    mychatarchive info                Show archive stats
    mychatarchive mcp-config          Print MCP configuration JSON
"""

import argparse
import json
import sys
from pathlib import Path

from mychatarchive import __version__
from mychatarchive.config import get_db_path


def _add_db_arg(parser):
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=f"Path to SQLite database (default: {get_db_path()})",
    )


def _cli_scope(args) -> tuple:
    """Resolve --include-private/--include-sealed flags into a sensitivity scope."""
    if getattr(args, "include_sealed", False):
        print("WARNING: including SEALED content in output.", file=sys.stderr)
        return ("public", "private", "sealed")
    if getattr(args, "include_private", False):
        return ("public", "private")
    return ("public",)


def main():
    parser = argparse.ArgumentParser(
        prog="mychatarchive",
        description="Local-first AI memory archive. Import, embed, search, and serve your chat history.",
    )
    parser.add_argument("--version", action="version", version=f"mychatarchive {__version__}")

    sub = parser.add_subparsers(dest="command")

    # --- init ---
    sub.add_parser("init", help="Interactive setup - configure storage, embeddings, sources, and drop folder")

    # --- sync ---
    sync_p = sub.add_parser(
        "sync",
        help="One-command import: auto-discovers Claude Code + Cursor, scans drop folder, pulls named sources",
    )
    sync_p.add_argument("--embed", action="store_true", help="Also run embedding after sync")
    _add_db_arg(sync_p)

    # --- sources ---
    sources_p = sub.add_parser("sources", help="Manage import sources (named paths to pull from)")
    sources_sub = sources_p.add_subparsers(dest="sources_command")

    sources_add = sources_sub.add_parser("add", help="Add a named import source")
    sources_add.add_argument("name", help="Source name (e.g. 'nas', 'desktop', 'exports')")
    sources_add.add_argument("path", help="Path to file or directory")
    sources_add.add_argument("--format", default=None, help="Force format (chatgpt, anthropic, etc.)")
    sources_add.add_argument("--account", default="main", help="Account identifier")

    sources_rm = sources_sub.add_parser("remove", help="Remove a source")
    sources_rm.add_argument("name", help="Source name to remove")

    sources_sub.add_parser("list", help="List all configured sources")

    sources_rename = sources_sub.add_parser("rename", help="Rename a source")
    sources_rename.add_argument("old", help="Current name")
    sources_rename.add_argument("new", help="New name")

    # --- import ---
    import_p = sub.add_parser(
        "import", help="Import chat history (files, directories, sources, or 'auto')"
    )
    import_p.add_argument(
        "file", type=str, nargs="?", default=None,
        help="Path to export file/directory, or 'auto' for Claude Code/Cursor",
    )
    import_p.add_argument(
        "--from", dest="source", default=None,
        help="Import from a named source (see 'mychatarchive sources list'), or 'all'",
    )
    import_p.add_argument(
        "--format",
        choices=["chatgpt", "anthropic", "grok", "claude_code", "cursor"],
        default=None,
        help="Export format (auto-detected if not specified)",
    )
    import_p.add_argument("--account", default="main", help="Account identifier")
    import_p.add_argument("--source-id", default=None, help="Import batch ID")
    _add_db_arg(import_p)

    # --- export ---
    export_p = sub.add_parser(
        "export", help="Export archive to JSON, CSV, or a standalone SQLite copy"
    )
    export_p.add_argument(
        "output", type=str,
        help="Output file path (.json, .csv, or .db/.sqlite for SQLite copy)",
    )
    export_p.add_argument(
        "--format",
        choices=["json", "csv", "sqlite"],
        default=None,
        help="Output format (auto-detected from extension if not specified)",
    )
    export_p.add_argument(
        "--platform",
        default=None,
        help="Filter by platform (chatgpt, anthropic, grok, claude_code, cursor)",
    )
    export_p.add_argument(
        "--include-thoughts", action="store_true",
        help="Include captured thoughts in the export",
    )
    export_p.add_argument(
        "--include-private", action="store_true",
        help="Include content classified as private (default: public only)",
    )
    export_p.add_argument(
        "--include-sealed", action="store_true",
        help="Include SEALED content (implies --include-private; prints a warning)",
    )
    _add_db_arg(export_p)

    # --- embed ---
    embed_p = sub.add_parser("embed", help="Generate vector embeddings for all messages")
    embed_p.add_argument("--batch-size", type=int, default=64, help="Embedding batch size")
    embed_p.add_argument("--force", action="store_true", help="Re-embed all messages")
    _add_db_arg(embed_p)

    # --- summarize ---
    summarize_p = sub.add_parser(
        "summarize",
        help="Generate LLM thread summaries (pipeline step: after sync, before embed)",
    )
    summarize_p.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help="LLM model (OpenAI-compatible name). Falls back to config.json summarize.model, then anthropic/claude-haiku-4-5.",
    )
    summarize_p.add_argument(
        "--base-url",
        default=None,
        metavar="URL",
        help="API base URL. Falls back to config.json summarize.base_url, then OpenRouter.",
    )
    summarize_p.add_argument(
        "--key",
        default=None,
        metavar="APIKEY",
        help="API key (falls back to OPENROUTER_API_KEY or ANTHROPIC_API_KEY env vars)",
    )
    summarize_p.add_argument(
        "--force",
        action="store_true",
        help="Re-summarize threads that already have summaries",
    )
    summarize_p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process at most N threads (useful for testing)",
    )
    summarize_p.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip embedding the summaries (faster, but disables thread-level semantic search)",
    )
    summarize_p.add_argument(
        "--messages-per-segment",
        type=int,
        default=None,
        metavar="N",
        help="Messages per summary segment. Threads with more messages get multiple summaries. Default: 15.",
    )
    summarize_p.add_argument(
        "--include-private",
        action="store_true",
        help="Also summarize private threads (sends their text to the LLM API). "
             "Sealed threads are never summarized.",
    )
    _add_db_arg(summarize_p)

    # --- groups ---
    groups_p = sub.add_parser(
        "groups",
        help="Manage thread groups — curate which threads belong to Jarvis, Coding, Projects, etc.",
    )
    groups_sub = groups_p.add_subparsers(dest="groups_command")

    groups_list = groups_sub.add_parser("list", help="List all groups with thread counts")
    _add_db_arg(groups_list)

    groups_create = groups_sub.add_parser("create", help="Create a new group")
    groups_create.add_argument("name", help="Group name (slug, e.g. 'jarvis', 'coding')")
    groups_create.add_argument("--description", default=None, help="Optional description")
    _add_db_arg(groups_create)

    groups_add = groups_sub.add_parser("add", help="Add threads to a group")
    groups_add.add_argument("group", help="Group name")
    groups_add.add_argument(
        "thread_ids",
        nargs="+",
        metavar="THREAD_ID",
        help="canonical_thread_id(s) to add (use 'mychatarchive groups show <group>' to find IDs)",
    )
    _add_db_arg(groups_add)

    groups_remove = groups_sub.add_parser("remove", help="Remove threads from a group")
    groups_remove.add_argument("group", help="Group name")
    groups_remove.add_argument("thread_ids", nargs="+", metavar="THREAD_ID")
    _add_db_arg(groups_remove)

    groups_delete = groups_sub.add_parser("delete", help="Delete a group (threads are not deleted)")
    groups_delete.add_argument("name", help="Group name to delete")
    _add_db_arg(groups_delete)

    groups_show = groups_sub.add_parser("show", help="Show threads in a group")
    groups_show.add_argument("name", help="Group name")
    groups_show.add_argument("--limit", type=int, default=50, help="Max threads to show")
    _add_db_arg(groups_show)

    # --- classify ---
    classify_p = sub.add_parser(
        "classify",
        help="Set sensitivity levels (public/private/sealed) so agents only retrieve what you allow",
    )
    classify_sel = classify_p.add_mutually_exclusive_group(required=True)
    classify_sel.add_argument(
        "--thread", metavar="THREAD_ID",
        help="Classify one thread (applies immediately)",
    )
    classify_sel.add_argument(
        "--query", metavar="TEXT",
        help="Bulk-classify every thread containing a keyword match (preview first; apply with --confirm)",
    )
    classify_sel.add_argument(
        "--before", metavar="YYYY-MM-DD",
        help="Bulk-classify threads whose last message predates this date (preview first; apply with --confirm)",
    )
    classify_sel.add_argument(
        "--list", action="store_true", dest="list_counts",
        help="Show row counts per sensitivity level",
    )
    classify_p.add_argument(
        "--level", choices=["public", "private", "sealed"], default=None,
        help="Sensitivity level to apply (required except with --list)",
    )
    classify_p.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would change without applying (bulk selectors preview by default)",
    )
    classify_p.add_argument(
        "--confirm", action="store_true",
        help="Actually apply a bulk classification (--query/--before never apply without this)",
    )
    classify_p.add_argument(
        "--limit", type=int, default=500,
        help="Max threads to show in a bulk preview (default: 500)",
    )
    _add_db_arg(classify_p)

    # --- serve ---
    serve_p = sub.add_parser("serve", help="Start MCP server")
    serve_p.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default=None,
        help="MCP transport (default: from config or stdio)",
    )
    serve_p.add_argument(
        "--port",
        type=int,
        default=8420,
        help="Port for SSE transport (default: 8420)",
    )
    serve_p.add_argument(
        "--ssl-certfile",
        default=None,
        metavar="PATH",
        help="Path to TLS certificate PEM file (enables HTTPS)",
    )
    serve_p.add_argument(
        "--ssl-keyfile",
        default=None,
        metavar="PATH",
        help="Path to TLS private key PEM file",
    )
    _add_db_arg(serve_p)

    # --- search ---
    search_p = sub.add_parser("search", help="Search your archive from the command line")
    search_p.add_argument("query", nargs="+", help="Search query")
    search_p.add_argument("--limit", type=int, default=10, help="Max results")
    search_p.add_argument(
        "--mode",
        choices=["semantic", "keyword"],
        default="semantic",
        help="Search mode (default: semantic)",
    )
    search_p.add_argument(
        "--platform",
        action="append",
        metavar="PLATFORM",
        help="Filter by platform (repeat for multiple: --platform chatgpt --platform anthropic)",
    )
    search_p.add_argument(
        "--hours",
        type=int,
        default=None,
        help="Only include messages from the last N hours",
    )
    search_p.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Only include messages from this date onward",
    )
    search_p.add_argument(
        "--sort",
        choices=["relevance", "time"],
        default="relevance",
        help="Sort results by relevance (default) or time (newest first)",
    )
    search_p.add_argument(
        "--group",
        default=None,
        metavar="GROUP",
        help="Filter to threads in this group (see 'mychatarchive groups list')",
    )
    search_p.add_argument(
        "--include-private", action="store_true",
        help="Include content classified as private (default: public only)",
    )
    search_p.add_argument(
        "--include-sealed", action="store_true",
        help="Include SEALED content (implies --include-private; prints a warning)",
    )
    _add_db_arg(search_p)

    # --- info ---
    info_p = sub.add_parser("info", help="Show archive database stats")
    _add_db_arg(info_p)

    # --- mcp-config ---
    mcp_p = sub.add_parser("mcp-config", help="Print MCP server configuration JSON")
    mcp_p.add_argument(
        "--client",
        choices=["claude-desktop", "cursor"],
        default="claude-desktop",
        help="Target MCP client",
    )
    _add_db_arg(mcp_p)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "init":
        _cmd_init()
        return

    if args.command == "sources":
        _cmd_sources(args)
        return

    db_path = getattr(args, "db", None) or get_db_path()

    if args.command == "sync":
        _cmd_sync(args, db_path)
    elif args.command == "import":
        _cmd_import(args, db_path)
    elif args.command == "export":
        _cmd_export(args, db_path)
    elif args.command == "embed":
        _cmd_embed(args, db_path)
    elif args.command == "summarize":
        _cmd_summarize(args, db_path)
    elif args.command == "groups":
        _cmd_groups(args, db_path)
    elif args.command == "classify":
        _cmd_classify(args, db_path)
    elif args.command == "serve":
        _cmd_serve(args, db_path)
    elif args.command == "search":
        _cmd_search(args, db_path)
    elif args.command == "info":
        _cmd_info(db_path)
    elif args.command == "mcp-config":
        _cmd_mcp_config(args, db_path)


def _cmd_init():
    from mychatarchive.config import (
        load_config, save_config, get_config_path,
        ensure_drop_folder, _DEFAULT_DROP_FOLDER, _AUTO_SOURCE_DEFAULTS,
        _DEFAULT_CHUNK_SIZE, _DEFAULT_CHUNK_OVERLAP,
    )

    existing = load_config()
    cfg = {
        "storage": existing.get("storage", {}),
        "embeddings": existing.get("embeddings", {}),
        "transport": existing.get("transport", {}),
        "drop_folder": existing.get("drop_folder", _DEFAULT_DROP_FOLDER),
        "auto_sources": existing.get("auto_sources", dict(_AUTO_SOURCE_DEFAULTS)),
        "sources": existing.get("sources", {}),
    }

    print("MyChatArchive Setup")
    print("=" * 50)

    # --- Drop folder ---
    print()
    print("DROP FOLDER")
    print("  This is where you drop raw chat exports (JSON files).")
    print("  Anything placed here gets imported on 'mychatarchive sync'.")
    current_drop = cfg["drop_folder"]
    drop_input = input(f"  Drop folder [{current_drop}]: ").strip()
    if drop_input:
        cfg["drop_folder"] = drop_input

    # --- Auto-discovery ---
    print()
    print("AUTO-DISCOVERY")
    print("  These tools are auto-detected from their default locations.")

    for name, default in _AUTO_SOURCE_DEFAULTS.items():
        current = cfg["auto_sources"].get(name, default)
        label = "enabled" if current else "disabled"
        toggle = input(f"  {name} [{label}] (y/n/Enter to keep): ").strip().lower()
        if toggle == "y":
            cfg["auto_sources"][name] = True
        elif toggle == "n":
            cfg["auto_sources"][name] = False

    # --- Storage ---
    print()
    print("STORAGE")
    storage_options = {"1": "sqlite"}
    current_storage = cfg["storage"].get("backend", "sqlite")
    print(f"  Backend (current: {current_storage}):")
    print("    1. sqlite  - Local SQLite file (default)")
    print("    2. postgres - PostgreSQL (coming soon)")
    choice = input("  Choose (default 1): ").strip() or "1"
    if choice == "2":
        print("    Postgres support is coming soon. Using sqlite for now.")
        choice = "1"
    cfg["storage"]["backend"] = storage_options.get(choice, "sqlite")

    current_path = cfg["storage"].get("path", "~/.mychatarchive/archive.db")
    path_input = input(f"  Database path [{current_path}]: ").strip()
    if path_input:
        cfg["storage"]["path"] = path_input
    elif "path" not in cfg["storage"]:
        cfg["storage"]["path"] = current_path

    # --- Embeddings ---
    print()
    print("EMBEDDINGS")
    current_embed = cfg["embeddings"].get("backend", "local")
    print(f"  Backend (current: {current_embed}):")
    print("    1. local  - sentence-transformers, runs on your machine (default)")
    print("    2. openai - OpenAI API (text-embedding-3-small/large, best quality)")
    print("    3. openrouter - OpenRouter API embeddings (coming soon)")
    choice = input("  Choose (default 1): ").strip() or "1"
    if choice == "1":
        cfg["embeddings"]["backend"] = "local"
    elif choice == "2":
        cfg["embeddings"]["backend"] = "openai"
        cfg["embeddings"]["model"] = "text-embedding-3-small"
        cfg["embeddings"]["dimension"] = 1536
        api_key = cfg["embeddings"].get("openai_api_key") or ""
        key_input = input(
            "  OpenAI API key (or press Enter to use OPENAI_API_KEY env later): "
        ).strip()
        if key_input:
            cfg["embeddings"]["openai_api_key"] = key_input
        model_choice = input(
            "  Model [1=small/1536dim, 2=large/3072dim] (default 1): "
        ).strip() or "1"
        if model_choice == "2":
            cfg["embeddings"]["model"] = "text-embedding-3-large"
            cfg["embeddings"]["dimension"] = 3072
    else:
        print("    OpenRouter coming soon. Using local.")
        cfg["embeddings"]["backend"] = "local"

    # --- Chunking ---
    print()
    print("CHUNKING")
    print("  Long messages are split into overlapping chunks for better semantic search.")
    print("  1,200 chars ~ 300 tokens - fits local models and OpenAI's limits.")
    current_chunk_size = cfg["embeddings"].get("chunk_size", _DEFAULT_CHUNK_SIZE)
    cs_input = input(f"  Chunk size in chars [{current_chunk_size}]: ").strip()
    if cs_input:
        try:
            cfg["embeddings"]["chunk_size"] = int(cs_input)
        except ValueError:
            print(f"    Invalid value, keeping {current_chunk_size}.")
    else:
        cfg["embeddings"]["chunk_size"] = current_chunk_size

    current_overlap = cfg["embeddings"].get("chunk_overlap", _DEFAULT_CHUNK_OVERLAP)
    ov_input = input(f"  Overlap between chunks [{current_overlap}]: ").strip()
    if ov_input:
        try:
            cfg["embeddings"]["chunk_overlap"] = int(ov_input)
        except ValueError:
            print(f"    Invalid value, keeping {current_overlap}.")
    else:
        cfg["embeddings"]["chunk_overlap"] = current_overlap

    # --- Transport ---
    print()
    print("MCP TRANSPORT")
    transport_options = {"1": "stdio", "2": "sse"}
    current_transport = cfg["transport"].get("type", "stdio")
    print(f"  Transport (current: {current_transport}):")
    print("    1. stdio - Local pipe, for Claude Desktop/Cursor (default)")
    print("    2. sse   - HTTP server, for remote/mobile access")
    choice = input("  Choose (default 1): ").strip() or "1"
    cfg["transport"]["type"] = transport_options.get(choice, "stdio")

    if cfg["transport"]["type"] == "sse":
        current_port = cfg["transport"].get("port", 8420)
        port_input = input(f"  SSE port [{current_port}]: ").strip()
        if port_input:
            try:
                cfg["transport"]["port"] = int(port_input)
            except ValueError:
                cfg["transport"]["port"] = current_port
        else:
            cfg["transport"]["port"] = current_port

    # Save + create drop folder
    print()
    save_config(cfg)
    drop_path = ensure_drop_folder()

    print(f"Config saved to {get_config_path()}")
    print(f"Drop folder created at {drop_path}")
    print()
    print("=" * 50)
    print("You're all set! Here's your workflow:")
    print()
    print("  1. Drop raw exports into your drop folder:")
    print(f"     {drop_path}")
    print()
    print("  2. Sync everything with one command:")
    print("     mychatarchive sync")
    print()
    print("  This auto-imports from:")
    for name, enabled in cfg["auto_sources"].items():
        status = "ON" if enabled else "OFF"
        print(f"     [{status}] {name}")
    print(f"     [ON]  Drop folder ({drop_path})")
    if cfg["sources"]:
        for name in cfg["sources"]:
            print(f"     [ON]  {name}")
    print()
    print("  3. Generate embeddings + start serving:")
    print("     mychatarchive embed && mychatarchive serve")


def _cmd_sync(args, db_path: Path):
    from mychatarchive.ingest import run_all
    run_all(db_path)

    if args.embed:
        print("\nRunning embeddings...", file=sys.stderr)
        from mychatarchive.embeddings import run as embed_run
        embed_run(db_path=db_path)


def _cmd_sources(args):
    from mychatarchive.config import get_sources, add_source, remove_source, rename_source

    cmd = args.sources_command

    if cmd is None:
        _cmd_sources_list()
        return

    if cmd == "add":
        add_source(args.name, args.path, format_name=args.format, account=args.account)
        print(f"Source '{args.name}' added: {args.path}")
        if args.format:
            print(f"  Format: {args.format}")
        print(f"  Account: {args.account}")
        print(f"\nUse: mychatarchive import --from {args.name}")

    elif cmd == "remove":
        if remove_source(args.name):
            print(f"Source '{args.name}' removed.")
        else:
            print(f"Source '{args.name}' not found.", file=sys.stderr)
            sys.exit(1)

    elif cmd == "list":
        _cmd_sources_list()

    elif cmd == "rename":
        if rename_source(args.old, args.new):
            print(f"Source renamed: '{args.old}' -> '{args.new}'")
        else:
            print(
                f"Cannot rename: '{args.old}' not found or '{args.new}' already exists.",
                file=sys.stderr,
            )
            sys.exit(1)


def _cmd_sources_list():
    from mychatarchive.config import get_sources, get_auto_sources, get_drop_folder

    auto = get_auto_sources()
    drop = get_drop_folder()
    sources = get_sources()

    print("All import sources (used by 'mychatarchive sync'):")
    print(f"{'─' * 60}")

    # Auto-discovery
    print("  AUTO-DISCOVERY:")
    for name, enabled in auto.items():
        status = "ON " if enabled else "OFF"
        print(f"    [{status}] {name}")

    # Drop folder
    print()
    drop_exists = "✓" if drop.exists() else "✗"
    file_count = len(list(drop.rglob("*"))) if drop.exists() else 0
    importable = sum(1 for p in drop.rglob("*") if p.is_file() and p.suffix.lower() in (".json", ".jsonl")) if drop.exists() else 0
    print(f"  DROP FOLDER:")
    print(f"    Path:  {drop} [{drop_exists}]")
    if drop.exists():
        print(f"    Files: {importable} importable")

    # Named sources
    if sources:
        print()
        print(f"  NAMED SOURCES ({len(sources)}):")
        for name, cfg in sources.items():
            path = cfg.get("path", "?")
            fmt = cfg.get("format", "auto-detect")
            account = cfg.get("account", "main")
            exists = "✓" if Path(path).expanduser().exists() else "✗"
            print(f"    {name}")
            print(f"      Path:    {path} [{exists}]")
            print(f"      Format:  {fmt}")
            print(f"      Account: {account}")

    print(f"{'─' * 60}")
    print(f"\nSync all:  mychatarchive sync")
    print(f"One source: mychatarchive import --from <name>")
    print(f"Add source: mychatarchive sources add <name> <path>")


def _cmd_import(args, db_path: Path):
    # --from takes priority: import from named source(s)
    if args.source:
        if args.source.lower() == "all":
            from mychatarchive.ingest import run_all
            run_all(db_path)
        else:
            from mychatarchive.ingest import run_source
            run_source(args.source, db_path)
        return

    # Direct file/directory/auto path
    file_str = args.file
    if file_str is None:
        print(
            "Provide a file path, directory, or use --from <source>.\n"
            "Examples:\n"
            "  mychatarchive import conversations.json\n"
            "  mychatarchive import ./exports/\n"
            "  mychatarchive import --from nas\n"
            "  mychatarchive import --from all\n"
            "  mychatarchive sync               (same as --from all)",
            file=sys.stderr,
        )
        sys.exit(1)

    if file_str.lower() == "auto":
        fmt = args.format
        if fmt is None:
            print(
                "When using 'auto', specify --format (claude_code or cursor).",
                file=sys.stderr,
            )
            sys.exit(1)
        from mychatarchive.ingest import run
        run(
            file_path=Path("auto"),
            db_path=db_path,
            format_name=fmt,
            platform=fmt,
            account_id=args.account,
            source_id=args.source_id,
        )
        return

    file_path = Path(file_str)
    if not file_path.exists():
        print(f"Path not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    if file_path.is_dir():
        from mychatarchive.ingest import run_directory
        run_directory(
            dir_path=file_path,
            db_path=db_path,
            format_name=args.format,
            account_id=args.account,
            source_id=args.source_id,
        )
    else:
        from mychatarchive.ingest import run
        run(
            file_path=file_path,
            db_path=db_path,
            format_name=args.format,
            account_id=args.account,
            source_id=args.source_id,
        )


def _cmd_export(args, db_path: Path):
    if not db_path.exists():
        print(f"No database at {db_path}. Import chats first.", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output)
    fmt = args.format
    if fmt is None:
        ext = output_path.suffix.lower()
        fmt_map = {".json": "json", ".csv": "csv", ".db": "sqlite", ".sqlite": "sqlite"}
        fmt = fmt_map.get(ext)
        if fmt is None:
            print(
                f"Cannot detect format from extension '{ext}'. "
                f"Use --format (json, csv, sqlite).",
                file=sys.stderr,
            )
            sys.exit(1)

    from mychatarchive import db

    scope = _cli_scope(args)

    if fmt == "sqlite":
        # A file copy carries EVERYTHING — every table, including sealed rows
        # and their vectors. It cannot be filtered, so it is refused when
        # sealed content exists unless the caller explicitly opts in.
        import shutil
        con = db.get_connection(db_path)
        db.ensure_schema(con)
        has_sealed = db.sealed_exists(con)
        if has_sealed and not args.include_sealed:
            con.close()
            print(
                "This archive contains SEALED content, and a SQLite copy cannot "
                "be filtered — the file carries every row. Re-run with "
                "--include-sealed to copy anyway, or use --format json/csv for "
                "a filtered export.",
                file=sys.stderr,
            )
            sys.exit(1)
        if "private" not in scope or has_sealed:
            print(
                "Note: a SQLite copy includes ALL content regardless of "
                "sensitivity flags (private" + (" and sealed" if has_sealed else "")
                + " rows are in the file).",
                file=sys.stderr,
            )
        # Flush WAL frames into the main file so the copy is complete.
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
        shutil.copy2(str(db_path), str(output_path))
        print(f"Database copied to {output_path}")
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"  Size: {size_mb:.1f} MB")
        return

    con = db.get_connection(db_path)
    db.ensure_schema(con)
    messages = db.export_messages(con, platform=args.platform, scope=scope)
    thoughts = []
    if args.include_thoughts:
        thoughts = db.export_thoughts(con, scope=scope)
    con.close()

    if fmt == "json":
        export_data = {"messages": messages, "count": len(messages)}
        if thoughts:
            export_data["thoughts"] = thoughts
            export_data["thought_count"] = len(thoughts)
        if args.platform:
            export_data["platform_filter"] = args.platform

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

        print(f"Exported {len(messages):,} messages to {output_path}")
        if thoughts:
            print(f"  + {len(thoughts):,} thoughts")

    elif fmt == "csv":
        import csv
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["message_id", "thread_id", "platform", "account_id",
                            "timestamp", "role", "content", "title", "source_id",
                            "sensitivity"],
            )
            writer.writeheader()
            writer.writerows(messages)

        print(f"Exported {len(messages):,} messages to {output_path}")
        if thoughts and args.include_thoughts:
            thought_path = output_path.with_name(output_path.stem + "_thoughts.csv")
            with open(thought_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["thought_id", "content", "created_at", "metadata"],
                )
                writer.writeheader()
                writer.writerows(thoughts)
            print(f"  + {len(thoughts):,} thoughts to {thought_path}")


def _cmd_embed(args, db_path: Path):
    if not db_path.exists():
        print(f"No database found at {db_path}. Import chats first.", file=sys.stderr)
        sys.exit(1)

    from mychatarchive.embeddings import run
    run(db_path=db_path, batch_size=args.batch_size, force=args.force)


def _cmd_summarize(args, db_path: Path):
    if not db_path.exists():
        print(f"No database found at {db_path}. Import chats first.", file=sys.stderr)
        sys.exit(1)

    from mychatarchive.config import load_config
    from mychatarchive.summarizer import (
        run as summarize_run, _DEFAULT_MODEL, _DEFAULT_BASE_URL, _DEFAULT_MESSAGES_PER_SEGMENT,
    )

    summarize_cfg = load_config().get("summarize", {})
    model = args.model or summarize_cfg.get("model") or _DEFAULT_MODEL
    base_url = args.base_url or summarize_cfg.get("base_url") or _DEFAULT_BASE_URL
    messages_per_segment = (
        args.messages_per_segment
        or summarize_cfg.get("messages_per_segment")
        or _DEFAULT_MESSAGES_PER_SEGMENT
    )

    print(f"Generating thread summaries...", file=sys.stderr)
    print(f"  Model:    {model}", file=sys.stderr)
    print(f"  Base URL: {base_url}", file=sys.stderr)
    print(f"  Segment:  {messages_per_segment} messages/segment", file=sys.stderr)
    if args.limit:
        print(f"  Limit:    {args.limit} threads", file=sys.stderr)

    try:
        stats = summarize_run(
            db_path=db_path,
            model=model,
            base_url=base_url,
            api_key=args.key or "",
            force=args.force,
            limit=args.limit,
            embed_summaries=not args.no_embed,
            messages_per_segment=messages_per_segment,
            include_private=getattr(args, "include_private", False),
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print()
    print(f"Summarization complete:")
    print(f"  Processed: {stats['processed']:,} threads ({stats.get('segments', 0):,} segments)")
    print(f"  Skipped:   {stats['skipped']:,} (already summarized)")
    print(f"  Errors:    {stats['errors']}")
    if stats["processed"] > 0:
        print()
        print("Next steps:")
        print("  mychatarchive embed          # embed messages for chunk-level search")
        print("  mychatarchive groups create <name>  # organize threads into groups")


def _cmd_groups(args, db_path: Path):
    if not db_path.exists():
        print(f"No database found at {db_path}. Import chats first.", file=sys.stderr)
        sys.exit(1)

    cmd = getattr(args, "groups_command", None)
    if cmd is None:
        _cmd_groups_list(db_path)
        return

    if cmd == "list":
        _cmd_groups_list(db_path)

    elif cmd == "create":
        import datetime as dt
        import hashlib
        from mychatarchive import db
        con = db.get_connection(db_path)
        db.ensure_schema(con)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        group_id = hashlib.sha1(args.name.encode()).hexdigest()[:12]
        ok = db.create_group(con, group_id, args.name, args.description, now)
        con.close()
        if ok:
            print(f"Group '{args.name}' created (id: {group_id})")
        else:
            print(f"Group '{args.name}' already exists.", file=sys.stderr)
            sys.exit(1)

    elif cmd == "add":
        import datetime as dt
        from mychatarchive import db
        con = db.get_connection(db_path)
        db.ensure_schema(con)
        group_row = db.get_group_by_name(con, args.group)
        if not group_row:
            print(f"Group '{args.group}' not found. Create it first:", file=sys.stderr)
            print(f"  mychatarchive groups create {args.group}", file=sys.stderr)
            sys.exit(1)
        group_id = group_row[0]
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        added = 0
        for tid in args.thread_ids:
            if db.add_to_group(con, tid, group_id, now):
                added += 1
        con.commit()
        con.close()
        print(f"Added {added}/{len(args.thread_ids)} thread(s) to '{args.group}'.")

    elif cmd == "remove":
        from mychatarchive import db
        con = db.get_connection(db_path)
        group_row = db.get_group_by_name(con, args.group)
        if not group_row:
            print(f"Group '{args.group}' not found.", file=sys.stderr)
            sys.exit(1)
        group_id = group_row[0]
        removed = 0
        for tid in args.thread_ids:
            if db.remove_from_group(con, tid, group_id):
                removed += 1
        con.commit()
        con.close()
        print(f"Removed {removed}/{len(args.thread_ids)} thread(s) from '{args.group}'.")

    elif cmd == "delete":
        from mychatarchive import db
        con = db.get_connection(db_path)
        group_row = db.get_group_by_name(con, args.name)
        if not group_row:
            print(f"Group '{args.name}' not found.", file=sys.stderr)
            sys.exit(1)
        group_id = group_row[0]
        db.delete_group(con, group_id)
        con.close()
        print(f"Group '{args.name}' deleted.")

    elif cmd == "show":
        from mychatarchive import db
        con = db.get_connection(db_path)
        group_row = db.get_group_by_name(con, args.name)
        if not group_row:
            print(f"Group '{args.name}' not found.", file=sys.stderr)
            sys.exit(1)
        group_id = group_row[0]
        threads = db.get_threads_in_group(con, group_id)
        con.close()

        if not threads:
            print(f"Group '{args.name}' has no threads yet.")
            print(f"  mychatarchive groups add {args.name} <thread_id> ...")
            return

        print(f"Group: {args.name} ({len(threads)} threads)")
        print(f"{'─' * 60}")
        for t in threads[: args.limit]:
            title = (t["title"] or "Untitled")[:50]
            ts = (t["ts_start"] or "")[:10]
            msgs = t["message_count"]
            plat = t["platform"] or "?"
            print(f"  [{plat}] {ts}  {title}  ({msgs} msgs)")
            print(f"    {t['canonical_thread_id']}")


def _cmd_groups_list(db_path: Path):
    from mychatarchive import db
    con = db.get_connection(db_path)
    db.ensure_schema(con)
    groups = db.list_groups(con)
    con.close()

    if not groups:
        print("No groups yet.")
        print()
        print("Create your first group:")
        print("  mychatarchive groups create jarvis --description 'Daily personal chats'")
        print("  mychatarchive groups create coding --description 'Dev work and technical threads'")
        return

    print(f"Thread Groups ({len(groups)}):")
    print(f"{'─' * 60}")
    for group_id, name, description, created_at, member_count in groups:
        desc = f" — {description}" if description else ""
        print(f"  {name}{desc}  [{member_count} threads]")
        print(f"    id: {group_id}")
    print()
    print("Commands:")
    print("  mychatarchive groups show <name>         list threads in a group")
    print("  mychatarchive groups add <name> <id...>  add threads to a group")
    print("  mychatarchive search 'query' --group <name>  search within a group")


def _cmd_classify(args, db_path: Path):
    if not db_path.exists():
        print(f"No database found at {db_path}. Import chats first.", file=sys.stderr)
        sys.exit(1)

    from mychatarchive import db

    con = db.get_connection(db_path)
    db.ensure_schema(con)  # write path — also the migration trigger

    if args.list_counts:
        counts = db.sensitivity_counts(con)
        con.close()
        print(f"Sensitivity levels - {db_path}")
        print(f"{'-' * 60}")
        header = f"  {'':<18}{'public':>10}{'private':>10}{'sealed':>10}"
        print(header)
        for label, key in (("Threads", "threads"), ("Messages", "messages"),
                           ("Thoughts", "thoughts"), ("Summaries", "thread_summaries")):
            row = counts[key]
            print(f"  {label:<18}{row.get('public', 0):>10,}"
                  f"{row.get('private', 0):>10,}{row.get('sealed', 0):>10,}")
        print()
        print("Commands:")
        print("  mychatarchive classify --thread <id> --level private")
        print("  mychatarchive classify --query 'text' --level sealed --confirm")
        print("  mychatarchive classify --before 2023-01-01 --level private --confirm")
        return

    if not args.level:
        print("--level is required (public, private, or sealed).", file=sys.stderr)
        con.close()
        sys.exit(1)

    def _warn_if_sealed():
        if args.level == "sealed":
            print(
                "WARNING: sealed content is never returned by the MCP server and "
                "is hidden from search/export unless you pass --include-sealed.",
                file=sys.stderr,
            )

    if args.thread:
        current = db.get_thread_sensitivity(con, args.thread)
        if current is None:
            print(f"Thread '{args.thread}' not found.", file=sys.stderr)
            con.close()
            sys.exit(1)
        _warn_if_sealed()
        counts = db.set_thread_sensitivity(con, [args.thread], args.level)
        con.close()
        print(f"Thread {args.thread}: {current[0]} -> {args.level}")
        print(f"  Updated: {counts['messages']} messages, {counts['chunks']} chunks, "
              f"{counts['thread_summaries']} summary segments")
        return

    # Bulk selectors: --query / --before. Preview by default, apply only
    # with --confirm — this is how six years of backlog get classified
    # without reading them, so it must never fire by accident.
    if args.query:
        # Classification tooling must see everything, or already-classified
        # content could never be re-classified.
        matches = db.fts_search(con, args.query, limit=10000,
                                scope=db.SENSITIVITY_LEVELS)
        thread_ids = sorted({row[2] for row in matches})
        selector_desc = f"threads with keyword match for {args.query!r}"
    else:
        import datetime
        try:
            datetime.datetime.strptime(args.before, "%Y-%m-%d")
        except ValueError:
            print("Invalid --before format. Use YYYY-MM-DD.", file=sys.stderr)
            con.close()
            sys.exit(1)
        thread_ids = db.threads_before(con, args.before)
        selector_desc = f"threads whose last message predates {args.before}"

    if not thread_ids:
        print(f"No {selector_desc}. Nothing to do.")
        con.close()
        return

    # Preview: level distribution + a capped listing.
    print(f"{len(thread_ids):,} {selector_desc}")
    print(f"{'-' * 60}")
    shown = 0
    total_msgs = 0
    for tid in thread_ids:
        info = db.get_thread_sensitivity(con, tid)
        if info is None:
            continue
        level, msg_count = info
        total_msgs += msg_count
        if shown < args.limit:
            print(f"  {tid}  [{level}, {msg_count} msgs]")
            shown += 1
    if shown < len(thread_ids):
        print(f"  ... and {len(thread_ids) - shown:,} more (raise --limit to see them)")
    print()
    print(f"Would set {len(thread_ids):,} threads / {total_msgs:,} messages "
          f"(plus their chunks and summaries) to '{args.level}'.")

    if args.dry_run or not args.confirm:
        con.close()
        if args.dry_run:
            print("\nDry run - nothing changed.")
        else:
            print("\nPreview only - re-run with --confirm to apply.")
        return

    _warn_if_sealed()
    counts = db.set_thread_sensitivity(con, thread_ids, args.level)
    con.close()
    print(f"\nApplied '{args.level}' to {len(thread_ids):,} threads: "
          f"{counts['messages']:,} messages, {counts['chunks']:,} chunks, "
          f"{counts['thread_summaries']:,} summary segments.")


def _cmd_serve(args, db_path: Path):
    if not db_path.exists():
        print(f"No database found at {db_path}. Import and embed chats first.", file=sys.stderr)
        sys.exit(1)

    transport = args.transport
    if transport is None:
        from mychatarchive.backends import get_transport
        transport = get_transport()

    from mychatarchive.mcp.server import run
    run(
        db_path=db_path,
        transport=transport,
        port=args.port,
        ssl_certfile=getattr(args, "ssl_certfile", None),
        ssl_keyfile=getattr(args, "ssl_keyfile", None),
    )


def _cmd_search(args, db_path: Path):
    if not db_path.exists():
        print(f"No database found at {db_path}. Import chats first.", file=sys.stderr)
        sys.exit(1)

    query = " ".join(args.query)
    from mychatarchive import db
    import datetime

    con = db.get_connection(db_path)
    db.ensure_schema(con)
    scope = _cli_scope(args)
    platform = args.platform if args.platform else None
    sort_by_time = args.sort == "time"

    cutoff_iso = None
    if args.hours is not None:
        cutoff_iso = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=args.hours)
        ).isoformat()
    elif args.since:
        try:
            dt = datetime.datetime.strptime(args.since, "%Y-%m-%d")
            cutoff_iso = dt.replace(tzinfo=datetime.timezone.utc).isoformat()
        except ValueError:
            print(f"Invalid --since format. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)

    # Resolve group filter to a set of thread IDs
    group_thread_ids = None
    if hasattr(args, "group") and args.group:
        group_row = db.get_group_by_name(con, args.group)
        if not group_row:
            print(f"Group '{args.group}' not found. Use 'mychatarchive groups list'.",
                  file=sys.stderr)
            sys.exit(1)
        group_thread_ids = db.get_group_thread_ids(con, group_row[0])
        if not group_thread_ids:
            print(f"Group '{args.group}' has no threads. Add some first:")
            print(f"  mychatarchive groups add {args.group} <thread_id>")
            con.close()
            return

    if args.mode == "semantic":
        from mychatarchive.embeddings import embed_single
        embedding = embed_single(query)
        results = db.search_chunks(
            con, embedding, limit=args.limit, platform=platform,
            cutoff_iso=cutoff_iso, sort_by_time=sort_by_time,
            group_thread_ids=group_thread_ids, scope=scope,
        )

        if not results:
            print("No results found.")
            con.close()
            return

        for i, (chunk_id, distance) in enumerate(results, 1):
            row = db.get_chunk_by_id(con, chunk_id, scope=scope)
            if row:
                meta = json.loads(row[4]) if row[4] else {}
                similarity = round(1.0 - distance, 4)
                title = meta.get("title", "Untitled")
                role = meta.get("role", "?")
                print(f"\n--- Result {i} (similarity: {similarity}) ---")
                print(f"Thread: {title}")
                print(f"Role: {role} | Time: {row[2]}")
                print(f"{row[0][:500]}")
    else:
        results = db.fts_search(
            con, query, limit=args.limit, platform=platform,
            cutoff_iso=cutoff_iso, sort_by_time=sort_by_time,
            group_thread_ids=group_thread_ids, scope=scope,
        )
        if not results:
            print("No results found.")
            con.close()
            return

        for i, row in enumerate(results, 1):
            print(f"\n--- Result {i} ---")
            print(f"Thread: {row[5] or 'Untitled'}")
            print(f"Role: {row[4]} | Time: {row[3]}")
            print(f"{row[1][:500]}")

    con.close()


def _cmd_info(db_path: Path):
    if not db_path.exists():
        print(f"No database at {db_path}. Import chats first.", file=sys.stderr)
        sys.exit(1)

    from mychatarchive import db

    try:
        con = db.get_connection(db_path)
    except Exception:
        import sqlite3
        con = sqlite3.connect(str(db_path))

    msgs = db.message_count(con)
    threads = db.thread_count(con)
    chunks = db.chunk_count(con)
    thoughts = db.thought_count(con)
    summaries = db.summary_count(con)
    groups = db.group_count(con)
    try:
        platforms = db.platform_counts(con)
    except Exception:
        platforms = []
    # Guarded like platform_counts: the plain-sqlite3 fallback connection may
    # be a pre-v3 archive with no sensitivity column.
    try:
        sens_threads = db.sensitivity_counts(con).get("threads", {})
    except Exception:
        sens_threads = {}
    con.close()

    print(f"MyChatArchive - {db_path}")
    print(f"{'-' * 40}")
    print(f"  Messages:    {msgs:,}")
    print(f"  Threads:     {threads:,}")
    print(f"  Summaries:   {summaries:,}")
    print(f"  Embedded:    {chunks:,} chunks")
    print(f"  Thoughts:    {thoughts:,}")
    print(f"  Groups:      {groups:,}")
    if sens_threads:
        parts = [f"{level}: {n:,}" for level, n in sorted(sens_threads.items())]
        print(f"  Sensitivity: {' | '.join(parts)} (threads)")
    if platforms:
        print(f"  Platforms:")
        for plat, count in platforms:
            print(f"    {plat}: {count:,}")

    if chunks == 0 and msgs > 0:
        print(f"\n  Tip: Run 'mychatarchive embed' to enable semantic search.")
    if summaries == 0 and threads > 0:
        print(f"  Tip: Run 'mychatarchive summarize' to generate thread summaries"
              f" (enables groups + profile tool).")


def _cmd_mcp_config(args, db_path: Path):
    """Print the MCP configuration snippet for the requested client."""
    import shutil

    mychatarchive_path = shutil.which("mychatarchive")
    if mychatarchive_path is None:
        mychatarchive_path = "mychatarchive"

    if args.client == "claude-desktop":
        config = {
            "mcpServers": {
                "mychatarchive": {
                    "command": mychatarchive_path,
                    "args": ["serve", "--db", str(db_path)],
                }
            }
        }
        print("Add this to your Claude Desktop config file:")
        if sys.platform == "win32":
            print(r"  %APPDATA%\Claude\claude_desktop_config.json")
        else:
            print("  ~/Library/Application Support/Claude/claude_desktop_config.json")
        print()
        print(json.dumps(config, indent=2))

    elif args.client == "cursor":
        config = {
            "mcpServers": {
                "mychatarchive": {
                    "command": mychatarchive_path,
                    "args": ["serve", "--db", str(db_path)],
                }
            }
        }
        print("Add this to your Cursor MCP settings:")
        print()
        print(json.dumps(config, indent=2))


if __name__ == "__main__":
    main()
