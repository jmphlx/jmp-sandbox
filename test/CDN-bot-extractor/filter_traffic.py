"""
CDN Traffic Filter Script
-------------------------
Filters CDN log files by removing entries that match user agents in the exclusion list.

Usage:
    python filter_traffic.py
    python filter_traffic.py --log publish_cdn_2026-03-02.log --exclusions agent-exclusions.csv --output filtered_traffic.csv
"""

import csv
import json
import argparse
import glob
from pathlib import Path
from datetime import datetime


def load_exclusion_user_agents(exclusion_file: str) -> set:
    """
    Load user agents to exclude from CSV file.
    
    Args:
        exclusion_file: Path to CSV with 'request_user_agent' column
        
    Returns:
        Set of user agent strings to exclude
    """
    print(f"Loading exclusions from: {exclusion_file}")
    user_agents = set()
    
    with open(exclusion_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ua = row.get('request_user_agent', '')
            if ua:
                user_agents.add(str(ua))
    
    print(f"✓ Loaded {len(user_agents)} unique user agents to exclude")
    return user_agents


def find_log_file(pattern: str = "publish_cdn_*.log") -> str:
    """
    Find the CDN log file matching the pattern.
    
    Args:
        pattern: Glob pattern to match log files
        
    Returns:
        Path to the first matching log file
    """
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(f"No files found matching pattern: {pattern}")
    
    # Return the most recent file if multiple matches
    matches.sort(reverse=True)
    return matches[0]


def filter_log_file(log_file: str, exclusion_agents: set, output_file: str):
    """
    Filter the CDN log file to show only BOT traffic that was filtered out.
    
    Args:
        log_file: Path to the CDN log file (JSON lines format)
        exclusion_agents: Set of user agents to exclude
        output_file: Path for the filtered output CSV
    """
    print(f"\nProcessing log file: {log_file}")
    
    total_lines = 0
    kept_lines = 0
    excluded_lines = 0
    error_lines = 0
    
    # Collect ONLY the excluded (bot) rows for output
    bot_rows = []
    fieldnames = None
    
    with open(log_file, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
                
            total_lines += 1
            
            try:
                data = json.loads(line)
                user_agent = data.get('req_ua', '')
                
                # Check if this user agent should be excluded
                if user_agent in exclusion_agents:
                    excluded_lines += 1
                    # Add to bot output
                    bot_rows.append(data)
                    if fieldnames is None:
                        fieldnames = list(data.keys())
                else:
                    kept_lines += 1
                    
            except json.JSONDecodeError as e:
                error_lines += 1
                if error_lines <= 5:
                    print(f"  Warning: Could not parse line {line_num}: {str(e)[:50]}")
            
            # Progress update every 100,000 lines
            if total_lines % 100000 == 0:
                print(f"  Processed {total_lines:,} lines... (normal: {kept_lines:,}, bots: {excluded_lines:,})")
    
    print(f"\n✓ Processing complete!")
    print(f"  Total lines:    {total_lines:,}")
    print(f"  Normal Traffic: {kept_lines:,} ({kept_lines/total_lines*100:.2f}%)")
    print(f"  Bot Traffic:    {excluded_lines:,} ({excluded_lines/total_lines*100:.2f}%)")
    if error_lines > 0:
        print(f"  Parse errors:   {error_lines:,}")
    
    # Save ONLY bot traffic as CSV
    print(f"\nSaving bot traffic to: {output_file}")
    
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        # Write summary stats as header
        bot_pct = excluded_lines / total_lines * 100 if total_lines > 0 else 0
        normal_pct = kept_lines / total_lines * 100 if total_lines > 0 else 0
        
        f.write(f"# CDN Traffic Analysis - BOT TRAFFIC ONLY\n")
        f.write(f"# Total Requests: {total_lines:,}\n")
        f.write(f"# Normal Traffic: {kept_lines:,} ({normal_pct:.2f}%)\n")
        f.write(f"# Bot Traffic (shown below): {excluded_lines:,} ({bot_pct:.2f}%)\n")
        f.write(f"#\n")
        
        # Write CSV data - ONLY BOT ROWS
        if bot_rows and fieldnames:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(bot_rows)
    
    print(f"✓ Saved {len(bot_rows):,} bot traffic rows to {output_file}")
    
    return {
        'total': total_lines,
        'kept': kept_lines,
        'excluded': excluded_lines,
        'errors': error_lines
    }


def main():
    parser = argparse.ArgumentParser(
        description='Filter CDN traffic logs - outputs ONLY bot traffic that was filtered'
    )
    parser.add_argument(
        '--log',
        default=None,
        help='Path to CDN log file (default: auto-detect publish_cdn_*.log)'
    )
    parser.add_argument(
        '--exclusions',
        default='agent-exclusions.csv',
        help='Path to exclusion CSV file (default: agent-exclusions.csv)'
    )
    parser.add_argument(
        '--output',
        default=None,
        help='Output file path (default: bot_traffic_<timestamp>.csv)'
    )
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("CDN Traffic Filter - Bot Traffic Report")
    print("=" * 70)
    
    # Load exclusion list
    exclusion_agents = load_exclusion_user_agents(args.exclusions)
    
    # Find or use specified log file
    if args.log:
        log_file = args.log
    else:
        log_file = find_log_file("publish_cdn_*.log")
        print(f"Auto-detected log file: {log_file}")
    
    # Generate output filename if not specified
    if args.output:
        output_file = args.output
    else:
        # Create output filename based on input log file
        log_path = Path(log_file)
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        output_file = f"bot_traffic_{log_path.stem}_{timestamp}.csv"
    
    # Filter the log file
    results = filter_log_file(log_file, exclusion_agents, output_file)
    
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"Input:          {log_file}")
    print(f"Output:         {output_file}")
    print(f"Bot Traffic:    {results['excluded']:,} requests ({results['excluded']/results['total']*100:.2f}%)")
    print(f"Normal Traffic: {results['kept']:,} requests ({results['kept']/results['total']*100:.2f}%)")
    print("=" * 70)


if __name__ == '__main__':
    main()
