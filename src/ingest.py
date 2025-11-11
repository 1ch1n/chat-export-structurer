# ingest.py
# General-purpose LLM conversation export ingestion tool
# Supports ChatGPT, Anthropic Claude, and Grok exports

import argparse
import os
import sqlite3
import hashlib
import re
import datetime
from parsers import chatgpt, anthropic, grok

PARSERS = {
    "chatgpt": chatgpt,
    "anthropic": anthropic,
    "grok": grok
}

def norm_text(s):
    """Normalize text for stable ID generation."""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", s).strip().lower()

def sha1(s: str) -> str:
    """Generate SHA1 hash of string."""
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

def iso_from_epoch(ts):
    """Convert epoch timestamp to ISO format."""
    if ts is None or ts == 0:
        return None
    try:
        return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).replace(microsecond=0).isoformat()
    except Exception:
        return None

def round_epoch_seconds(ts):
    """Round epoch timestamp to nearest second."""
    if ts is None:
        return None
    try:
        return int(round(float(ts)))
    except Exception:
        return None

def ensure_schema(db_path: str):
    """Create SQLite schema with FTS support."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS messages (
      message_id TEXT PRIMARY KEY,
      canonical_thread_id TEXT NOT NULL,
      platform TEXT NOT NULL,
      account_id TEXT NOT NULL,
      ts TEXT NOT NULL,
      role TEXT NOT NULL,
      text TEXT NOT NULL,
      title TEXT,
      source_id TEXT NOT NULL
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
    USING fts5(text, content='');
    CREATE TABLE IF NOT EXISTS messages_fts_docids (
      rowid INTEGER PRIMARY KEY,
      message_id TEXT NOT NULL
    );
    """)
    con.commit()
    con.close()

def main():
    ap = argparse.ArgumentParser(
        description="Ingest LLM conversation exports into SQLite",
        epilog="Supported formats: chatgpt, anthropic, grok"
    )
    ap.add_argument("--in", dest="in_path", required=True,
                    help="Path to export file (JSON)")
    ap.add_argument("--db", dest="db_path",
                    help="Path to SQLite database (required unless --test)")
    ap.add_argument("--format", dest="format", required=True,
                    choices=list(PARSERS.keys()),
                    help="Export format: chatgpt, anthropic, or grok")
    ap.add_argument("--platform", default=None,
                    help="Platform name (defaults to format)")
    ap.add_argument("--account", dest="account_id", default="main",
                    help="Account identifier")
    ap.add_argument("--source-id", dest="source_id", default="src_0001",
                    help="Unique ID for this import batch")
    ap.add_argument("--test", action="store_true",
                    help="Test mode: show parsed messages without writing to DB")
    args = ap.parse_args()

    # Test mode doesn't require --db
    if not args.test and not args.db_path:
        ap.error("--db is required unless using --test mode")

    platform = args.platform or args.format

    # Load parser
    parser = PARSERS[args.format]
    
    print(f"\n[*] Parsing {args.format} export: {args.in_path}\n")
    
    # Parse messages
    messages = list(parser.parse(args.in_path))
    
    if not messages:
        print("[!] No messages found in export")
        return
    
    print(f"[+] Parsed {len(messages)} messages from {len(set(m['thread_id'] for m in messages))} threads\n")
    
    # Test mode: just show sample and exit
    if args.test:
        print("[TEST MODE] Sample messages:\n")
        
        # Show first 5 messages
        for i, msg in enumerate(messages[:5], 1):
            print(f"Message {i}:")
            print(f"  Thread: {msg['thread_title'][:50] or '(no title)'}")
            print(f"  Role: {msg['role']}")
            print(f"  Content: {msg['content'][:100]}...")
            print(f"  Created: {iso_from_epoch(msg['created_at'])}")
            print()
        
        if len(messages) > 5:
            print(f"... and {len(messages) - 5} more messages")
        
        # Show thread statistics
        threads = {}
        for msg in messages:
            tid = msg['thread_id']
            if tid not in threads:
                threads[tid] = {"title": msg['thread_title'], "count": 0}
            threads[tid]["count"] += 1
        
        print(f"\n[STATS] Thread Statistics:")
        print(f"  Total threads: {len(threads)}")
        print(f"  Average messages per thread: {len(messages) / len(threads):.1f}")
        print(f"\n  Top 5 threads by message count:")
        for i, (tid, data) in enumerate(sorted(threads.items(), key=lambda x: x[1]["count"], reverse=True)[:5], 1):
            print(f"    {i}. {data['title'][:60] or '(no title)'}: {data['count']} messages")
        
        print("\n[+] Test complete - no database was modified")
        return
    
    # Production mode: write to database
    db_dir = os.path.dirname(args.db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    ensure_schema(args.db_path)
    
    con = sqlite3.connect(args.db_path)
    cur = con.cursor()
    insert_msg = """INSERT OR IGNORE INTO messages
        (message_id, canonical_thread_id, platform, account_id, ts, role, text, title, source_id)
        VALUES (?,?,?,?,?,?,?,?,?)"""
    
    ins_count = 0
    dup_count = 0
    batch = 0
    
    # Group messages by thread
    threads = {}
    for msg in messages:
        tid = msg['thread_id']
        if tid not in threads:
            threads[tid] = []
        threads[tid].append(msg)
    
    print(f"[*] Writing to database: {args.db_path}\n")
    
    for thread_id, thread_messages in threads.items():
        # Sort by timestamp
        thread_messages.sort(key=lambda m: m['created_at'])
        
        first = thread_messages[0]
        first_snip = (first['content'] or "")[:256]
        
        # Generate canonical thread ID
        canonical_thread_id = sha1("|".join([
            platform,
            args.account_id,
            norm_text(first['thread_title']),
            str(round_epoch_seconds(first['created_at']) or ""),
            first['role'] or "",
            norm_text(first_snip)
        ]))
        
        cur.execute("BEGIN")
        for msg in thread_messages:
            ts_round = round_epoch_seconds(msg['created_at']) or 0
            message_id = sha1("|".join([
                platform, args.account_id, canonical_thread_id, msg['role'] or "",
                str(ts_round), norm_text(msg['content'] or "")
            ]))
            
            ts_iso = iso_from_epoch(msg['created_at'])
            if not ts_iso:
                continue
            
            cur.execute(insert_msg, (
                message_id, canonical_thread_id, platform, args.account_id,
                ts_iso, msg['role'] or "", msg['content'] or "",
                msg['thread_title'], args.source_id
            ))
            
            if cur.rowcount == 0:
                dup_count += 1
                continue
            
            # FTS insert
            cur.execute("INSERT INTO messages_fts (text) VALUES (?)", (msg['content'] or "",))
            fts_rowid = cur.execute("SELECT max(rowid) FROM messages_fts").fetchone()[0]
            cur.execute("INSERT INTO messages_fts_docids (rowid, message_id) VALUES (?,?)",
                       (fts_rowid, message_id))
            
            ins_count += 1
            batch += 1
            if batch >= 2000:
                con.commit()
                print(f"  [*] Committed batch ({ins_count} inserted, {dup_count} duplicates)")
                batch = 0
        con.commit()
    
    total = cur.execute("SELECT count(*) FROM messages").fetchone()[0]
    con.close()
    
    print(f"\n[+] Complete!")
    print(f"  Inserted: {ins_count}")
    print(f"  Duplicates skipped: {dup_count}")
    print(f"  Total messages in DB: {total}")

if __name__ == "__main__":
    main()

