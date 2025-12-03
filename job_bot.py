import streamlit as st
import requests
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from openai import OpenAI
from googleapiclient.discovery import build
import json
from bs4 import BeautifulSoup
import io
import pdfplumber

# --- CONFIGURATION ---
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
ADZUNA_APP_ID = st.secrets["ADZUNA_APP_ID"]
ADZUNA_APP_KEY = st.secrets["ADZUNA_APP_KEY"]
GMAIL_USER = st.secrets["GMAIL_USER"]
GMAIL_APP_PASSWORD = st.secrets["GMAIL_APP_PASSWORD"]
GOOGLE_API_KEY = st.secrets.get("GOOGLE_API_KEY")
SEARCH_ENGINE_ID = st.secrets.get("SEARCH_ENGINE_ID")

client = OpenAI(api_key=OPENAI_API_KEY)

# --- TARGET LISTS ---
# Tier 1 targets (Add more here to customize your "Dream" list)
TIER_1_DOMAINS = [
    "careers.microsoft.com", "amazon.jobs", "careers.google.com", 
    "netflix.com/jobs", "careers.apple.com", "meta.com/careers",
    "careers.deloitte.com", "accenture.com", "capgemini.com",
    "mckinsey.com/careers", "bcg.com/careers", "bain.com/careers",
    "dgrsystems.com", "bedroc.com", "salesforce.com/company/careers",
    "oracle.com/careers", "cisco.com/c/en/us/about/careers"
]

COUNTRY_MAP = {
    "usa": "us", "united states": "us", "australia": "au", 
    "uk": "gb", "germany": "de", "canada": "ca", "france": "fr",
    "netherlands": "nl"
}

# --- HELPER FUNCTIONS ---
def extract_text_from_pdf(uploaded_file):
    text = ""
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t: text += t + "\n"
        return text[:4000]
    except: return ""

# --- AI BRAIN ---
def parse_user_intent(dream_desc, resume_text):
    """
    Analyzes user intent to generate Search Keywords + Country List.
    """
    prompt = f"""
    You are a Global Headhunter. Plan a search strategy.
    
    USER DREAM: "{dream_desc}"
    USER RESUME: "{resume_text[:2000]}"
    
    TASK:
    1. Keywords: Generate 3 DISTINCT boolean search phrases (e.g. "Senior Systems Engineer", "Active Directory Architect").
    2. Countries: Identify target country codes (us, gb, au, ca, de). Default to 'us' if unsure.
    3. Remote: Boolean.
    
    OUTPUT JSON: {{ "keywords": [], "countries": [], "is_remote": true/false }}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]
        )
        return json.loads(response.choices[0].message.content)
    except: return None

def ai_analyze_job(job, dream_desc, resume_text):
    """
    Vibe Check: Rates the job against the user's specific dream.
    """
    prompt = f"""
    Rate this job for the user.
    USER WANTS: "{dream_desc}"
    USER SKILLS: "{resume_text[:1000]}"
    
    JOB: {job['Title']} @ {job['Company']}
    DESC: {job['Description'][:1000]}
    
    TASK:
    1. Score (0-100).
    2. Estimate Salary (e.g. "$120k").
    3. Reason (1 short sentence).
    
    OUTPUT JSON: "score" (int), "salary_est" (str), "reason" (str).
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]
        )
        return json.loads(response.choices[0].message.content)
    except:
        return {"score": 0, "salary_est": "N/A", "reason": "Error"}

# --- SEARCH ENGINES ---

def search_adzuna(term, country):
    """
    Aggregator Search - NOW FILTERED FOR FRESHNESS
    """
    results = []
    base_url = f"http://api.adzuna.com/v1/api/jobs/{country}/search/1"
    
    params = {
        'app_id': ADZUNA_APP_ID, 
        'app_key': ADZUNA_APP_KEY,
        'results_per_page': 15, 
        'what': term, 
        'sort_by': 'date',      # <--- FORCE NEWEST JOBS
        'max_days_old': 21,     # <--- KILL OLD CRAPPY JOBS (3 weeks max)
        'content-type': 'application/json'
    }
    try:
        resp = requests.get(base_url, params=params)
        data = resp.json()
        for item in data.get('results', []):
            results.append({
                'Title': item.get('title'),
                'Company': item.get('company', {}).get('display_name'),
                'Location': f"{item.get('location', {}).get('display_name')} ({country.upper()})",
                'Salary': item.get('salary_min', '0'),
                'Description': item.get('description', ''),
                'URL': item.get('redirect_url'),
                'Source': 'Adzuna (Fresh)'
            })
    except: pass
    return results

def search_tier_1_google(term, country_name):
    """
    X-Ray Search against Tier 1 Career Sites (Microsoft, Google, etc.)
    """
    if not GOOGLE_API_KEY: return []
    
    service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
    results = []
    
    # Chunk domains to avoid query length limits
    domain_chunks = [TIER_1_DOMAINS[i:i + 5] for i in range(0, len(TIER_1_DOMAINS), 5)]
    
    for chunk in domain_chunks:
        # Construct Query: (site:microsoft.com OR site:google.com) "Systems Engineer" "USA"
        site_operator = " OR ".join([f"site:{d}" for d in chunk])
        query = f"({site_operator}) {term} {country_name}"
        
        try:
            # We fetch 5 results per chunk to keep it fast
            res = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, num=5).execute()
            for item in res.get('items', []):
                title = item['title'].split("|")[0].split("-")[0].strip()
                results.append({
                    'Title': title,
                    'Company': item['displayLink'], 
                    'Location': country_name, 
                    'Salary': 'Check Site',
                    'Description': item['snippet'],
                    'URL': item['link'],
                    'Source': 'Tier 1 (Direct)'
                })
        except: pass
        
    return results

def run_hybrid_search(criteria):
    all_results = []
    seen_urls = set()
    
    target_countries = criteria.get('countries', ['us'])[:3] # Max 3 countries
    target_keywords = criteria.get('keywords', [])[:2]       # Max 2 keyword variations
    
    progress = st.empty()
    
    # THE HYBRID LOOP: For every country & keyword, we scan BOTH sources.
    for country in target_countries:
        c_code = COUNTRY_MAP.get(country.lower(), country.lower())
        
        for term in target_keywords:
            progress.text(f"🔍 Scanning {country.upper()}: '{term}' on Adzuna & Tier 1 sites...")
            
            # 1. Adzuna (Freshness Filtered)
            jobs_adz = search_adzuna(term, c_code)
            
            # 2. Tier 1 (Google X-Ray) - Always runs now
            jobs_t1 = search_tier_1_google(term, country)
            
            # Combine & Deduplicate
            for j in jobs_adz + jobs_t1:
                if j['URL'] not in seen_urls:
                    seen_urls.add(j['URL'])
                    all_results.append(j)
    
    progress.empty()
    return all_results

# --- EMAIL ---
def send_jobs_email(user_email, df):
    msg = MIMEMultipart()
    msg['Subject'] = f"Hybrid Job Search Results ({len(df)})"
    msg['From'] = GMAIL_USER
    msg['To'] = user_email
    
    # HTML Table
    html = df[['Match %', 'Title', 'Company', 'Source', 'Location']].to_html(index=False)
    msg.attach(MIMEText(f"<h3>Your Job Report</h3>{html}", 'html'))
    
    # Excel Attachment
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    part = MIMEApplication(excel_buffer.getvalue(), Name="Hybrid_Jobs.xlsx")
    part['Content-Disposition'] = 'attachment; filename="Hybrid_Jobs.xlsx"'
    msg.attach(part)
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        return True
    except: return False

# --- UI ---
st.set_page_config(page_title="Global Hybrid Hunter", page_icon="🌐", layout="wide")
st.title("🌐 Global Hybrid Hunter")
st.markdown("I scan **Job Aggregators** (for volume) AND **Tier 1 Career Sites** (for prestige) simultaneously.")

with st.form("job_form"):
    c1, c2 = st.columns([1, 1])
    with c1:
        dream_description = st.text_area("Search Criteria", height=150, 
            value="Senior Infrastructure Architect. Active Directory Migration. Remote/Travel. $130k+")
        uploaded_resume = st.file_uploader("Upload CV (PDF)", type=["pdf"])
    with c2:
        user_email = st.text_input("Email Results To", "judd@sharphuman.com")
        st.info("ℹ️ Filters: Jobs must be <21 days old. Searching US, EU, AU targets.")

    submitted = st.form_submit_button("Run Hybrid Search")

if submitted:
    resume_text = extract_text_from_pdf(uploaded_resume) if uploaded_resume else ""
    status = st.status("Initializing Hybrid Agent...", expanded=True)
    
    # 1. Plan
    criteria = parse_user_intent(dream_description, resume_text)
    
    if criteria:
        status.write(f"🗺️ Targets: **{criteria['countries']}**")
        status.write(f"🔑 Keywords: **{criteria['keywords']}**")
        
        # 2. Execute Hybrid Search
        raw_jobs = run_hybrid_search(criteria)
        
        if raw_jobs:
            status.write(f"👀 AI Scoring {len(raw_jobs)} candidates...")
            analyzed = []
            progress_bar = status.progress(0)
            
            for i, j in enumerate(raw_jobs):
                progress_bar.progress((i + 1) / len(raw_jobs))
                a = ai_analyze_job(j, dream_description, resume_text)
                
                j['Match %'] = a.get('score', 0)
                j['Salary Est.'] = a.get('salary_est', j['Salary'])
                j['Reason'] = a.get('reason', '')
                analyzed.append(j)
                
            df = pd.DataFrame(analyzed)
            # Filter low scores
            df = df[df['Match %'] > 50].sort_values(by='Match %', ascending=False).head(40)
            
            if not df.empty:
                send_jobs_email(user_email, df)
                status.update(label="✅ Done!", state="complete", expanded=False)
                st.success("Report Sent to Email!")
                
                for _, row in df.iterrows():
                    with st.expander(f"{row['Match %']}% {row['Title']} ({row['Source']})"):
                        st.write(f"**Company:** {row['Company']}")
                        st.write(f"**Reason:** {row['Reason']}")
                        st.write(f"**Salary:** {row['Salary Est.']}")
                        st.markdown(f"[Apply Now]({row['URL']})")
            else:
                status.update(label="No high matches", state="error")
                st.warning("Jobs found, but AI filtered them out based on your strict criteria.")
        else:
            status.update(label="No Jobs Found", state="error")
            st.error("0 Jobs found. Try relaxing your keyword constraints.")
