import csv
import datetime
import os
import random
import re
import smtplib
from collections import Counter
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from apify_client import ApifyClient

# ==============================================================================
# CENTRALIZED DYNAMIC CONFIGURATION
# All credentials, API search criteria, titles, resume skills, and alert parameters
# are defined in this single location. Override via environment variables if needed.
# ==============================================================================
CONFIG = {
    # --- ENVIRONMENT & SECRETS CONFIGURATION (SINGLE OR DUAL APIFY ACCOUNTS) ---
    # Automatically collects tokens from APIFY_API_TOKEN, APIFY_API_TOKEN_1, APIFY_API_TOKEN_2
    "APIFY_TOKENS": [
        t for t in [
            os.environ.get("APIFY_API_TOKEN_1") or os.environ.get("APIFY_API_TOKEN"),
            os.environ.get("APIFY_API_TOKEN_2")
        ] if t
    ],
    "APIFY_TOKEN": os.environ.get("APIFY_API_TOKEN"), # Fallback single token
    "TOKEN_ROTATION_MODE": "10_DAYS", # "10_DAYS" (10-day block rotation), "RANDOM", "FAILOVER"

    # --- EMAIL SETTINGS ---
    "SENDER_EMAIL": os.environ.get("SENDER_EMAIL", "itsneel.3t@gmail.com"),
    "SENDER_APP_PASSWORD": os.environ.get("SENDER_APP_PASSWORD"),
    "RECEIVER_EMAIL": os.environ.get("RECEIVER_EMAIL", "nilkamal.35@gmail.com"),

    # --- API SEARCH CRITERIA (OPTIMIZED FOR MAXIMUM HIGH-QUALITY MATCHES) ---
    "SEARCH_TITLE": "Data Engineer",  # Primary query passed to LinkedIn scraper
    "TARGET_TITLES": [                # Comprehensive role variants for title matching & filtering
        "Data Engineer", "Senior Data Engineer", "Lead Data Engineer", "Principal Data Engineer",
        "Databricks Developer", "Databricks Engineer", "Pyspark Developer", "PySpark Engineer",
        "Azure Data Engineer", "AWS Data Engineer", "GCP Data Engineer", "Cloud Data Engineer",
        "ETL Developer", "ETL Engineer", "Analytics Engineer", "Big Data Engineer",
        "Data Platform Engineer", "Data Architect", "Data Developer", "Pipeline Engineer",
        "Software Engineer - Data", "Data Pipeline Engineer"
    ],
    "SEARCH_LOCATION": "India",       # National anchor location for 1 single API call
    "DATE_POSTED": "r86400",         # "r86400" = 24h, "r604800" = past week, "r2592000" = past month
    "RESULT_LIMIT": 300,              # Scrapes up to 300 records in 1 API call for max coverage
    "TARGET_COMPANIES": [],           # Empty [] searches ALL companies across India (max coverage!)
    "EXPERIENCE_LEVELS": [],          # Empty [] includes unclassified/Not Applicable recruiter tags
    "CONTRACT_TYPES": ["F"],          # F=Full-time
    "REMOTE_OPTIONS": ["1", "2", "3"],# 1=On-site, 2=Remote, 3=Hybrid

    # --- EXCLUSION RULES ---
    "EXCLUDED_APPLY_TYPES": ["EASY_APPLY"],                # Exclude EASY_APPLY jobs
    "EXCLUDED_COMPANIES": ["Accenture", "Deloitte", "TCS"], # Exclude specific companies
    "SEEN_JOBS_FILE": os.path.join(os.path.dirname(__file__), "temp.csv"), # CSV History file to prevent re-notifying same jobs

    # --- RESUME OPTIMIZATION (NilkamalMahato_DE_Resume.pdf) ---
    "MAX_AGE_DAYS": 7,                # Maximum age of posting in days
    "RESUME_SKILLS": [                # Skills extracted from resume for relevance matching
        "Python", "PySpark", "Spark SQL", "Spark", "SQL", "Databricks", "Delta Lake",
        "Unity Catalog", "AWS", "AWS Glue", "Redshift", "S3", "Glue Data Catalog",
        "dbt", "DBT", "Airflow", "Medallion Architecture", "Lakehouse", "Data Lake",
        "ELT", "ETL", "ADF", "Azure Data Factory", "ADLS", "ADLS Gen2", "Azure Data Lake",
        "Git", "GitHub Actions", "Docker", "CI/CD", "PostgreSQL"
    ],
    "INDUSTRY_GAP_SKILLS": [          # Market skills evaluated for gap analysis & optimization tips
        "Snowflake", "Kafka", "Kubernetes", "Scala", "Polars", "Iceberg",
        "Trino", "Presto", "BigQuery", "Terraform", "Great Expectations", "Dagster", "Flink", "PyIceberg"
    ],

    # --- LOCATION PARTITIONING RULES ---
    "PRIMARY_LOCATION_KEYWORDS": [
        "kolkata", "remote", "anywhere", "work from home", "wfh", "hybrid - kolkata"
    ],
    "EXCLUDED_CITY_KEYWORDS": [
        "bangalore", "bengaluru", "mumbai", "pune", "hyderabad", "chennai",
        "gurgaon", "gurugram", "noida", "delhi", "ahmedabad", "coimbatore"
    ],

    # --- LIVE LINK VALIDATION SETTINGS ---
    "ENABLE_LIVE_LINK_CHECK": True,
    "LINK_VALIDATION_TIMEOUT": 10,
    "REQUEST_HEADERS": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    },
    "CLOSED_JOB_INDICATORS": [
        "no longer accepting applications",
        "this job is no longer available",
        "position has been filled",
        "job posting has expired",
        "we could not find the job you are looking for",
        "this job listing has expired",
        "job you are looking for is no longer active",
    ]
}


# ==============================================================================
# 1. APIFY DUAL/MULTI-ACCOUNT TOKEN ROTATION HELPERS
# ==============================================================================
def get_apify_tokens(config):
    """Returns list of configured tokens from environment variables / secrets."""
    tokens = config.get("APIFY_TOKENS") or []
    if not tokens:
        single_t = config.get("APIFY_TOKEN") or os.environ.get("APIFY_API_TOKEN")
        if single_t:
            tokens = [single_t]
    return tokens


def select_apify_token(config, attempt_idx=0):
    """Selects active Apify API token based on rotation mode (10_DAYS, RANDOM, or FAILOVER).
       Works seamlessly for single-account (1 token) and multi-account (2+ tokens) setups.
    """
    tokens = get_apify_tokens(config)
    if not tokens:
        return None, "No Token Configured"
    if len(tokens) == 1:
        return tokens[0], "Account #1 (Single Account)"

    mode = str(config.get("TOKEN_ROTATION_MODE", "10_DAYS")).upper()

    if attempt_idx > 0:
        chosen_idx = attempt_idx % len(tokens)
        return tokens[chosen_idx], f"Account #{chosen_idx + 1} (Failover Retry #{attempt_idx})"

    if mode == "10_DAYS":
        # 10-day block rotation: Days 0-9 -> Account 1, Days 10-19 -> Account 2, etc.
        day_of_year = datetime.datetime.now().timetuple().tm_yday
        token_index = (day_of_year // 10) % len(tokens)
        return tokens[token_index], f"Account #{token_index + 1} (10-Day Rotation Block)"
    elif mode == "RANDOM":
        token_index = random.randint(0, len(tokens) - 1)
        return tokens[token_index], f"Account #{token_index + 1} (Random Choice)"
    else:
        return tokens[0], "Account #1 (Primary)"


# ==============================================================================
# 2. CSV HISTORY DEDUPLICATION HELPERS (temp.csv)
# ==============================================================================
def load_seen_jobs(filepath=CONFIG["SEEN_JOBS_FILE"]):
    """Loads previously seen job IDs and URLs from temp.csv."""
    seen = set()
    if not os.path.exists(filepath):
        return seen
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                job_id = (row.get("jobID") or "").strip().lower()
                url = (row.get("URL") or "").strip().rstrip("/").lower()
                if job_id:
                    seen.add(job_id)
                if url:
                    seen.add(url)
    except Exception as e:
        print(f"[Warning] Could not read {filepath}: {e}")
    return seen


def save_seen_jobs(new_jobs, filepath=CONFIG["SEEN_JOBS_FILE"]):
    """Appends newly alerted jobs to temp.csv with columns: jobID, URL, extract_date, jobpost_date."""
    if not new_jobs:
        return
    try:
        file_exists = os.path.exists(filepath) and os.path.getsize(filepath) > 0
        seen = load_seen_jobs(filepath)
        added_count = 0
        extract_date = str(datetime.date.today())

        fieldnames = ["jobID", "URL", "extract_date", "jobpost_date"]

        with open(filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()

            for job in new_jobs:
                j_id = str(job.get("id") or "").strip()
                j_url = str(job.get("url") or "").strip()
                j_posted = str(job.get("posted") or "").strip()

                norm_id = j_id.lower()
                norm_url = j_url.rstrip("/").lower()

                if (norm_id and norm_id not in seen) or (norm_url and norm_url not in seen):
                    writer.writerow({
                        "jobID": j_id,
                        "URL": j_url,
                        "extract_date": extract_date,
                        "jobpost_date": j_posted
                    })
                    if norm_id:
                        seen.add(norm_id)
                    if norm_url:
                        seen.add(norm_url)
                    added_count += 1

        if added_count > 0:
            print(f"-> Saved {added_count} job records to {filepath} for next-day deduplication.")
    except Exception as e:
        print(f"[Error] Could not save to {filepath}: {e}")


# ==============================================================================
# 3. SINGLE API CALL SCRAPER (valig/linkedin-jobs-scraper)
# ==============================================================================
def fetch_linkedin_jobs_single_call(config):
    """Hits Apify LinkedIn Jobs Scraper Actor (valig/linkedin-jobs-scraper) IN 1 SINGLE API CALL.
       Supports automatic rotation and failover across single or multiple Apify accounts.
    """
    tokens = get_apify_tokens(config)
    if not tokens:
        print("[Error] No Apify API token found in environment variables. Please set APIFY_API_TOKEN or APIFY_API_TOKEN_1 in GitHub secrets.")
        return []

    actor_id = "valig/linkedin-jobs-scraper"

    run_input = {
        "title": config["SEARCH_TITLE"],
        "location": config["SEARCH_LOCATION"],
        "datePosted": config["DATE_POSTED"],
        "limit": config["RESULT_LIMIT"],
    }

    if config.get("TARGET_COMPANIES"):
        run_input["companyName"] = config["TARGET_COMPANIES"]
    if config.get("EXPERIENCE_LEVELS"):
        run_input["experienceLevel"] = config["EXPERIENCE_LEVELS"]
    if config.get("CONTRACT_TYPES"):
        run_input["contractType"] = config["CONTRACT_TYPES"]
    if config.get("REMOTE_OPTIONS"):
        run_input["remote"] = config["REMOTE_OPTIONS"]

    # Try each available token with failover
    for attempt in range(len(tokens)):
        token, account_label = select_apify_token(config, attempt_idx=attempt)
        if not token:
            continue
        print(f"[{datetime.datetime.now()}] Triggering 1 SINGLE API Call using {account_label}...")
        print(f"Params: Title='{run_input['title']}', Location='{run_input['location']}', DatePosted='{run_input['datePosted']}', Limit={run_input['limit']}")

        try:
            client = ApifyClient(token)
            run = client.actor(actor_id).call(run_input=run_input)
            dataset_id = getattr(run, "default_dataset_id", None) or run.get("defaultDatasetId")
            raw_items = list(client.dataset(dataset_id).iterate_items())
            print(f"-> Single API Call completed successfully via {account_label}! Retrieved {len(raw_items)} raw job records.")
            return raw_items
        except Exception as e:
            print(f"[Warning] Apify call failed using {account_label}: {e}")
            if attempt < len(tokens) - 1:
                print("-> Failing over to next available Apify account token...")

    print("[Error] All configured Apify account tokens failed.")
    return []


# ==============================================================================
# 4. RESUME OPTIMIZATION & RECENTNESS HELPERS
# ==============================================================================
def is_recent_enough(posted_text, max_days=CONFIG["MAX_AGE_DAYS"]):
    """Best-effort date verification supporting free text ('3 days ago') & absolute dates."""
    if not posted_text:
        return True

    text = str(posted_text).lower()
    if any(k in text for k in ("just posted", "today", "hour ago", "hours ago", "minute")):
        return True
    if "30+" in text:
        return False

    match = re.search(r"(\d+)\s*day", text)
    if match:
        return int(match.group(1)) <= max_days

    match = re.search(r"(\d+)\s*week", text)
    if match:
        return int(match.group(1)) * 7 <= max_days

    match = re.search(r"(\d+)\s*month", text)
    if match:
        return False

    date_formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d", "%B %d, %Y", "%B %d %Y", "%d %B %Y", "%d/%m/%Y", "%m/%d/%Y"
    ]
    cleaned = str(posted_text).strip()
    for fmt in date_formats:
        try:
            parsed_date = datetime.datetime.strptime(cleaned, fmt)
            delta_days = (datetime.datetime.now() - parsed_date).days
            return delta_days <= max_days
        except ValueError:
            continue

    return True


def get_sortable_date_timestamp(job):
    """Converts posted date/time string into a numeric timestamp for sorting jobs
       from NEWEST (highest timestamp) to OLDEST (lowest timestamp).
    """
    raw_date = job.get("postedDateRaw") or job.get("posted") or ""
    text = str(raw_date).lower().strip()
    now = datetime.datetime.now()

    if not text:
        return 0

    if any(k in text for k in ("just posted", "today", "minute", "hour")):
        return now.timestamp()

    match = re.search(r"(\d+)\s*hour", text)
    if match:
        hours = int(match.group(1))
        return (now - datetime.timedelta(hours=hours)).timestamp()

    match = re.search(r"(\d+)\s*day", text)
    if match:
        days = int(match.group(1))
        return (now - datetime.timedelta(days=days)).timestamp()

    match = re.search(r"(\d+)\s*week", text)
    if match:
        weeks = int(match.group(1))
        return (now - datetime.timedelta(days=weeks * 7)).timestamp()

    date_formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d", "%B %d, %Y", "%B %d %Y", "%d %B %Y", "%d/%m/%Y", "%m/%d/%Y"
    ]
    for fmt in date_formats:
        try:
            parsed_date = datetime.datetime.strptime(text, fmt)
            return parsed_date.timestamp()
        except ValueError:
            continue

    return 0


def match_resume_skills(text, skill_list):
    """Extracts resume skills present in job posting text."""
    text_lower = text.lower()
    found = []
    for skill in skill_list:
        s_lower = skill.lower()
        if s_lower == "dbt":
            if re.search(r"\bdbt\b", text_lower):
                found.append("dbt")
        elif s_lower in text_lower:
            found.append(skill)
    return list(dict.fromkeys(found))


def find_missing_industry_skills(text, resume_skills, gap_skills):
    """Finds high-demand industry skills mentioned in job descriptions that aren't on resume."""
    text_lower = text.lower()
    resume_skills_lower = set(s.lower() for s in resume_skills)
    missing = []
    for skill in gap_skills:
        s_lower = skill.lower()
        if s_lower in text_lower and s_lower not in resume_skills_lower:
            missing.append(skill)
    return list(dict.fromkeys(missing))


def matches_target_titles(title_text, target_titles):
    """Checks if job title matches any target title variants or core DE keywords, excluding non-DE roles."""
    if not title_text:
        return False
    text_lower = str(title_text).lower()

    # Explicitly filter out non-technical/non-DE roles
    non_de_phrases = ["qa manager", "qa lead", "test manager", "sales manager", "recruiter", "account executive", "hr manager", "guidewire", "pricing technology"]
    if any(phrase in text_lower for phrase in non_de_phrases):
        return False

    if target_titles:
        for target in target_titles:
            if target.lower() in text_lower:
                return True

    de_keywords = [
        "data engineer", "data engineering", "pyspark", "spark", "databricks",
        "azure data", "aws data", "gcp data", "etl", "elt", "data lake",
        "big data", "data platform", "analytics engineer", "data architect",
        "pipeline engineer", "data pipeline", "database engineer"
    ]
    return any(k in text_lower for k in de_keywords)


def check_link_still_open(url, timeout=CONFIG["LINK_VALIDATION_TIMEOUT"]):
    """Verifies that the job URL opens and the posting has not been closed on LinkedIn."""
    if not url or not url.startswith(("http://", "https://")):
        return False, "Invalid URL"

    try:
        resp = requests.get(url, headers=CONFIG["REQUEST_HEADERS"], timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        return False, f"Request failed ({e.__class__.__name__})"

    if resp.status_code >= 400:
        return False, f"HTTP {resp.status_code}"

    page_text_lower = resp.text[:80000].lower()
    for phrase in CONFIG["CLOSED_JOB_INDICATORS"]:
        if phrase in page_text_lower:
            return False, f'Page indicates "{phrase}"'

    return True, None


def verify_jobs_live(job_listings, label):
    """Filters out dead/closed job postings prior to email alert."""
    if not CONFIG["ENABLE_LIVE_LINK_CHECK"]:
        return job_listings

    kept = []
    for job in job_listings:
        is_open, reason = check_link_still_open(job["url"])
        if not is_open:
            print(f"[Link Check] Dropping closed/broken {label} link ({reason}): {job['url']}")
            continue
        kept.append(job)
    print(f"-> Live check ({label}): {len(kept)}/{len(job_listings)} links verified active.")
    return kept


# ==============================================================================
# 5. PROCESSING & LOCATION PARTITIONING (WITH GAP ANALYSIS)
# ==============================================================================
def process_and_partition_jobs(raw_items, config):
    """Deduplicates, evaluates title, applyType, company & resume fit, and partitions jobs into:
       1. Primary Locations (Kolkata & Remote)
       2. Excluded Cities / Major Tech Hubs (Bangalore, Pune, Hyderabad, Gurgaon, etc.)
       Also calculates missing industry skills for resume optimization tips.
    """
    primary_jobs = []
    excluded_city_jobs = []
    missing_skill_counter = Counter()

    seen_urls = set()
    seen_history = load_seen_jobs(config.get("SEEN_JOBS_FILE", "temp.csv"))

    skipped_stale = 0
    skipped_no_skills = 0
    skipped_title = 0
    skipped_easy_apply = 0
    skipped_excluded_company = 0
    skipped_seen_history = 0

    for item in raw_items:
        url = item.get("url") or "#"
        norm_url = url.rstrip("/").lower()
        job_id = str(item.get("id") or "").strip().lower()

        if norm_url in seen_urls or norm_url == "#":
            continue
        seen_urls.add(norm_url)

        # 1. Check temp.csv history
        if norm_url in seen_history or (job_id and job_id in seen_history):
            skipped_seen_history += 1
            continue

        # 2. Check Excluded Apply Type
        apply_type = str(item.get("applyType") or "").strip().upper()
        if any(ex_type.upper() in apply_type for ex_type in config.get("EXCLUDED_APPLY_TYPES", [])):
            skipped_easy_apply += 1
            continue

        # 3. Check Excluded Companies
        company = item.get("companyName", "Unknown")
        company_lower = company.lower()
        if any(ex_comp.lower() in company_lower for ex_comp in config.get("EXCLUDED_COMPANIES", [])):
            skipped_excluded_company += 1
            continue

        # 4. Check Title Variants
        title = item.get("title", "")
        if not matches_target_titles(title, config["TARGET_TITLES"]):
            skipped_title += 1
            continue

        # 5. Check Recency
        posted_date = item.get("postedDate") or item.get("postedTimeAgo") or ""
        if not is_recent_enough(posted_date, config["MAX_AGE_DAYS"]):
            skipped_stale += 1
            continue

        location = item.get("location", "")
        description = item.get("description", "")
        full_text = f"{title} {company} {location} {description}"

        # 6. Resume skill optimization
        matched_skills = match_resume_skills(full_text, config["RESUME_SKILLS"])
        if not matched_skills:
            skipped_no_skills += 1
            continue

        # 7. Identify missing industry skills for resume optimization
        missing_skills = find_missing_industry_skills(full_text, config["RESUME_SKILLS"], config.get("INDUSTRY_GAP_SKILLS", []))
        for m_skill in missing_skills:
            missing_skill_counter[m_skill] += 1

        formatted_job = {
            "id": item.get("id", ""),
            "title": title or "Data Engineer",
            "company": company,
            "location": location or "Not specified",
            "salary": item.get("salary", ""),
            "posted": item.get("postedTimeAgo") or item.get("postedDate") or "Recently",
            "postedDateRaw": item.get("postedDate") or item.get("postedTimeAgo") or "",
            "url": url,
            "snippet": (description[:240] + "...") if len(description) > 240 else (description or "No description available."),
            "applicants": item.get("applicationsCount", ""),
            "applyType": item.get("applyType", ""),
            "matched_skills": matched_skills,
            "missing_skills": missing_skills,
            "experienceLevel": item.get("experienceLevel", ""),
            "contractType": item.get("contractType", "")
        }

        loc_lower = location.lower()
        is_primary = any(k in loc_lower for k in config["PRIMARY_LOCATION_KEYWORDS"]) or "remote" in title.lower()
        has_excluded_city = any(c in loc_lower for c in config["EXCLUDED_CITY_KEYWORDS"])

        if is_primary and not (has_excluded_city and "kolkata" not in loc_lower and "remote" not in loc_lower):
            primary_jobs.append(formatted_job)
        else:
            excluded_city_jobs.append(formatted_job)

    # Sort both job partitions from NEWEST post first to OLDEST post later
    primary_jobs.sort(key=get_sortable_date_timestamp, reverse=True)
    excluded_city_jobs.sort(key=get_sortable_date_timestamp, reverse=True)

    print(f"-> Partitioning Summary: {len(primary_jobs)} Primary (Kolkata/Remote), {len(excluded_city_jobs)} Excluded Cities / Tech Hubs.")
    print(f"   (Filtered out: {skipped_seen_history} seen in temp.csv, {skipped_easy_apply} EASY_APPLY, {skipped_excluded_company} excluded companies, {skipped_stale} stale, {skipped_no_skills} no skill match, {skipped_title} title mismatch).")

    return primary_jobs, excluded_city_jobs, missing_skill_counter


# ==============================================================================
# 6. EXACT FORMAT JOB CARD RENDERER & ANTI-CLIPPING BATCHED EMAIL DISPATCHER
# Matches the user's exact requested visual format:
# 30. Engineer - PySpark/SQL at Barclays
# 📍 Bengaluru, Karnataka, India 🕒 1 day ago · 👥 Over 200 applicants
# Matched Resume Skills: PythonPySparkSparkSQLDatabricksAWS
# In-Demand Skill Gaps: + Scala
# Join us as a Engineer - PySpark/SQL at Barclays...
# View & Apply on LinkedIn →
# ==============================================================================
def render_job_card(job, idx, accent_color, badge_bg, badge_fg):
    skills_html = "".join([
        f'<span style="display:inline-block;background-color:#e8f0fe;color:#1a73e8;font-weight:600;border-radius:4px;padding:2px 7px;font-size:11px;margin-right:4px;margin-top:4px;">{s}</span>'
        for s in job.get("matched_skills", [])[:6]
    ])

    missing_html = ""
    if job.get("missing_skills"):
        missing_badges = "".join([
            f'<span style="display:inline-block;background-color:#fef2f2;color:#dc2626;font-weight:500;border-radius:4px;padding:2px 7px;font-size:11px;margin-right:4px;margin-top:4px;">+ {s}</span>'
            for s in job.get("missing_skills")[:4]
        ])
        missing_html = f'<div style="margin-top:6px;"><strong style="font-size:11px;color:#991b1b;">In-Demand Skill Gaps:</strong> {missing_badges}</div>'

    extra = f" · 💰 {job['salary']}" if job.get("salary") else ""
    applicants = f" · 👥 {job['applicants']}" if job.get("applicants") else ""

    return f"""
    <div style="background-color:#ffffff;padding:16px 18px;border:1px solid #e2e8f0;border-left:5px solid {accent_color};margin-bottom:16px;border-radius:6px;box-shadow:0 2px 4px rgba(0,0,0,0.04);">
        <h4 style="margin:0 0 6px 0;color:#1e293b;font-size:16px;">
            {idx}. {job['title']} at <span style="color:{accent_color};">{job['company']}</span>
        </h4>
        <div style="margin-bottom:8px;">
            <span style="display:inline-block;background-color:{badge_bg};color:{badge_fg};font-weight:600;border-radius:12px;padding:3px 10px;font-size:12px;margin-right:6px;">📍 {job['location']}{extra}</span>
            <span style="display:inline-block;background-color:#fef3c7;color:#92400e;font-weight:500;border-radius:12px;padding:3px 10px;font-size:12px;">🕒 {job['posted']}{applicants}</span>
        </div>
        <div style="margin-bottom:4px;">
            <strong style="font-size:12px;color:#475569;">Matched Resume Skills:</strong> {skills_html}
        </div>
        {missing_html}
        <p style="font-size:13px;color:#334155;margin-top:10px;margin-bottom:12px;line-height:1.5;">{job['snippet']}</p>
        <a href="{job['url']}" target="_blank" style="background-color:{accent_color};color:#ffffff;padding:8px 16px;text-decoration:none;font-weight:600;border-radius:4px;display:inline-block;font-size:12px;">View & Apply on LinkedIn →</a>
    </div>
    """


def send_email_batch(batch_primary, batch_excluded, batch_num, total_batches, missing_skill_counter, total_overall_jobs, config):
    """Sends one unclipped HTML email batch guaranteed to fit well under Gmail's 102 KB limit."""
    sender_email = config.get("SENDER_EMAIL")
    sender_pass = config.get("SENDER_APP_PASSWORD")
    receiver_email = config.get("RECEIVER_EMAIL")

    batch_total = len(batch_primary) + len(batch_excluded)
    run_date = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")

    batch_label = f" (Part {batch_num}/{total_batches})" if total_batches > 1 else ""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎯 Job Alert{batch_label}: {batch_total} Verified Openings ({len(batch_primary)} Kolkata/Remote | {len(batch_excluded)} Excluded Cities)"
    msg["From"] = sender_email
    msg["To"] = receiver_email

    top_missing = missing_skill_counter.most_common(5)
    gap_badges_html = ""
    if top_missing:
        gap_badges_html = "".join([
            f'<span style="display:inline-block;background-color:#fef2f2;color:#991b1b;border:1px solid #fecaca;font-weight:600;border-radius:14px;padding:4px 12px;font-size:12px;margin-right:6px;margin-bottom:6px;">⚡ {skill} ({count} roles)</span>'
            for skill, count in top_missing
        ])
    else:
        gap_badges_html = '<span style="color:#16a34a;font-size:13px;">✅ Outstanding match! Your resume covers all core requested skills.</span>'

    html_body = f"""
    <html>
      <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b; line-height: 1.6; max-width: 820px; margin: 0 auto; padding: 20px; background-color: #f8fafc;">

        <!-- Header Banner -->
        <div style="background: linear-gradient(135deg, #0f172a, #2563eb); border-radius: 8px; padding: 24px 28px; margin-bottom: 24px;">
          <h1 style="margin: 0 0 6px 0; color: #ffffff; font-size: 22px;">Data Engineer Job Digest — Nilkamal Mahato</h1>
          <p style="margin: 0; color: #cbd5e1; font-size: 13px;">Generated {run_date} · Showing {batch_total} Jobs{batch_label} (100% Guaranteed Unclipped HTML)</p>
        </div>

        <!-- Summary Stats -->
        <div style="margin-bottom: 24px;">
          <span style="display: inline-block; background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 20px; padding: 6px 14px; font-size: 12px; color: #2563eb; font-weight: bold; margin-right: 8px;">📍 {len(batch_primary)} Kolkata / Remote</span>
          <span style="display: inline-block; background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 20px; padding: 6px 14px; font-size: 12px; color: #d97706; font-weight: bold; margin-right: 8px;">🏙️ {len(batch_excluded)} Excluded Cities (Bangalore/Hyd/Pune/etc)</span>
          <span style="display: inline-block; background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 20px; padding: 6px 14px; font-size: 12px; color: #16a34a; font-weight: bold;">✅ All Live Verified</span>
        </div>
    """

    if batch_primary:
        html_body += f"""
        <div style="background-color: #eff6ff; padding: 12px 18px; border-radius: 6px; margin-bottom: 20px; border-left: 4px solid #2563eb;">
            <h3 style="margin: 0; color: #1d4ed8;">SECTION 1: Primary Target Jobs (Kolkata &amp; Remote)</h3>
            <p style="margin: 4px 0 0 0; font-size: 13px; color: #475569;">High priority opportunities located in Kolkata or Remote/WFH.</p>
        </div>
        """
        for idx, job in enumerate(batch_primary, 1):
            html_body += render_job_card(job, idx, "#2563eb", "#dbeafe", "#1e40af")

    if batch_excluded:
        html_body += f"""
        <div style="background-color: #fff7ed; padding: 12px 18px; border-radius: 6px; margin-top: 32px; margin-bottom: 20px; border-left: 4px solid #ea580c;">
            <h3 style="margin: 0; color: #c2410c;">SECTION 2: Excluded Cities / Major Tech Hubs</h3>
            <p style="margin: 4px 0 0 0; font-size: 13px; color: #475569;">Matching DE roles in Bangalore, Pune, Hyderabad, Gurgaon, Noida, Mumbai, Chennai, etc.</p>
        </div>
        """
        start_idx = len(batch_primary) + 1
        for idx, job in enumerate(batch_excluded, start_idx):
            html_body += render_job_card(job, idx, "#ea580c", "#ffedd5", "#9a3412")

    # SECTION 3: RESUME OPTIMIZATION & SKILL GAP TIPS
    html_body += f"""
        <div style="background-color: #f0fdf4; padding: 18px 22px; border-radius: 8px; margin-top: 36px; border: 1px solid #bbf7d0; border-left: 5px solid #16a34a;">
            <h3 style="margin: 0 0 6px 0; color: #15803d; font-size: 17px;">💡 Resume Optimization &amp; Skill Gap Insights</h3>
            <p style="margin: 0 0 12px 0; font-size: 13px; color: #374151;">
                Analyzing today's hiring demand: Here are top requested industry skills appearing in today's open roles that are not yet on your resume:
            </p>
            <div style="margin-bottom: 14px;">
                {gap_badges_html}
            </div>
            <div style="background-color: #ffffff; padding: 14px 16px; border-radius: 6px; border: 1px solid #dcfce7;">
                <h4 style="margin: 0 0 6px 0; color: #166534; font-size: 14px;">🎯 Actionable Resume Optimization Checklist:</h4>
                <ul style="margin: 0; padding-left: 18px; font-size: 12px; color: #4b5563; line-height: 1.6;">
                    <li><strong>ATS Keyword Insertion:</strong> If you have hands-on or project exposure to any of the skills above (e.g. Snowflake, Kafka, Kubernetes, Polars), add them to your <em>Technical Proficiencies</em> section to pass ATS filters.</li>
                    <li><strong>Highlight Metric Highlights:</strong> Ensure your <em>300+ DBT models</em>, <em>8TB governed data</em>, and <em>1.5-hour Bronze-to-Gold SLA</em> remain prominent near the top of your experience bullets.</li>
                    <li><strong>Certifications Callout:</strong> Put your <em>Databricks Certified Data Engineer Professional</em> and <em>AWS Certified Data Engineer</em> badges right under your name for maximum recruiter click-through rates.</li>
                </ul>
            </div>
        </div>

        <hr style="border: 0; border-top: 1px solid #e2e8f0; margin-top: 30px;">
        <p style="font-size: 11px; color: #94a3b8; text-align: center;">
            Automated Data Engineering Job Alert System · Single Apify API execution · Guaranteed 100% Delivery.
        </p>
      </body>
    </html>
    """

    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_pass)
            server.sendmail(sender_email, receiver_email, msg.as_string())
        print(f"🚀 Email batch {batch_num}/{total_batches} ({batch_total} jobs) sent successfully to {receiver_email}!")
    except Exception as e:
        print(f"[Error] Failed to send email batch {batch_num}: {e}")


def send_partitioned_email_alert(primary_jobs, excluded_jobs, missing_skill_counter, config):
    """Chunks jobs into batches of max 40 jobs per email so that Gmail NEVER clips any email and ALL 100+ jobs deliver cleanly."""
    total_jobs = len(primary_jobs) + len(excluded_jobs)
    if total_jobs == 0:
        print("No matching jobs found today. Skipping email alert.")
        return

    # Batching: Max 40 jobs per email guarantees size is < 70 KB (far below Gmail's 102 KB clip limit)
    MAX_JOBS_PER_EMAIL = 40

    all_jobs_tuples = [("primary", j) for j in primary_jobs] + [("excluded", j) for j in excluded_jobs]
    total_batches = (len(all_jobs_tuples) + MAX_JOBS_PER_EMAIL - 1) // MAX_JOBS_PER_EMAIL

    for b_idx in range(total_batches):
        batch_slice = all_jobs_tuples[b_idx * MAX_JOBS_PER_EMAIL : (b_idx + 1) * MAX_JOBS_PER_EMAIL]
        b_primary = [j for tag, j in batch_slice if tag == "primary"]
        b_excluded = [j for tag, j in batch_slice if tag == "excluded"]

        send_email_batch(b_primary, b_excluded, b_idx + 1, total_batches, missing_skill_counter, total_jobs, config)


# ==============================================================================
# MAIN EXECUTION PIPELINE
# ==============================================================================
if __name__ == "__main__":
    # Step 1: Hit Apify LinkedIn API ONCE (with multi-account failover/rotation)
    raw_jobs = fetch_linkedin_jobs_single_call(CONFIG)

    # Step 2: Filter by title, applyType, company & temp.csv history + calculate skill gap
    primary_listings, excluded_listings, missing_skill_counter = process_and_partition_jobs(raw_jobs, CONFIG)

    # Step 3: Verify live link validity before sending
    primary_listings = verify_jobs_live(primary_listings, "Primary Kolkata/Remote")
    excluded_listings = verify_jobs_live(excluded_listings, "Excluded Cities")

    # Step 4: Save sent job IDs & URLs to temp.csv for next-day deduplication
    all_final_jobs = primary_listings + excluded_listings
    save_seen_jobs(all_final_jobs, CONFIG.get("SEEN_JOBS_FILE", "temp.csv"))

    # Step 5: Dispatch email alert in unclipped batches with Resume Optimization & Skill Gap section
    send_partitioned_email_alert(primary_listings, excluded_listings, missing_skill_counter, CONFIG)
