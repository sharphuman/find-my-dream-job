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

# --- EXPANDED TIER 1 TARGETS ---
TIER_1_DOMAINS = [
    # Big Tech
    "careers.microsoft.com", "amazon.jobs", "careers.google.com", 
    "netflix.com/jobs", "careers.apple.com", "meta.com/careers",
    "salesforce.com/company/careers", "oracle.com/careers", 
    "cisco.com/c/en/us/about/careers", "ibm.com/careers", "intel.com/jobs",
    "nvidia.com/en-us/about-nvidia/careers", "adobe.com/careers",
    
    # Consulting & Enterprise
    "careers.deloitte.com", "accenture.com", "capgemini.com",
    "mckinsey.com/careers", "bcg.com/careers", "bain.com/careers",
    "kpmg.com/careers", "pwc.com/careers", "ey.com/careers",
    
    # Cloud/Infra Specific
    "vmware.com/careers", "redhat.com/en/jobs", "servicenow.com/careers",
    "workday.com/en-us/company/careers", "splunk.com/careers",
    "paloaltonetworks.com/company/careers", "fortinet.com/careers",
    
    # Specific requests
    "dgrsystems.com", "bedroc.com"
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
    Now generates 'broad_keywords' specifically for Tier 1 searching.
    """
    prompt = f"""
    You are a Global Headhunter. Plan a search strategy.
    
    USER DREAM: "{dream_desc}"
    USER RESUME: "{resume_text[:2000]}"
    
    TASK:
    1. specific_keywords: 2 very specific boolean phrases for Aggregators (e.g. "Senior Active Directory Architect").
    2. broad_keywords: 2 broader terms for Tier 1 Career Sites (e.g. just "Active Directory" or "Identity"). *Tier 1 sites have bad search engines, so we must be broad.*
    3. Countries: Target country codes.
    
    OUTPUT JSON: {{ "specific_keywords": [], "broad_keywords": [], "countries": [] }}
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
    prompt = f"""
    Rate this job.
    USER WANTS: "{dream_desc}"
    USER SKILLS: "{resume_text[:1000]}"
    
    JOB: {job['Title']} @ {job['Company']}
    DESC: {job['Description'][:1000]}
    
    TASK:
    1. Score (0-100).
    2. Estimate Salary.
    3. Reason.
    
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
    results = []
    base_url = f"http://api.adzuna.com/v1/api/jobs/{country}/search/1"
    params = {
        'app_id': ADZUNA_APP_ID, 'app_key': ADZUNA_APP_KEY,
        'results_per_page': 15, 'what': term, 'sort_by': 'date',
        'max_days_old': 21, 'content-type': 'application/json'
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
                'Source': 'Adzuna'
            })
    except: pass
    return results

def search_tier_1_google(term, country_name):
    """
    X-Ray Search using BROADER terms to get more hits.
    """
    if not GOOGLE_API_KEY: return []
    
    service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
    results = []
    
    # Chunk domains
    domain_chunks = [TIER_1_DOMAINS[i:i + 6] for i in range(0, len(TIER_1_DOMAINS), 6)]
    
    for chunk in domain_chunks:
        # Query: (site:microsoft.com OR site:google.com) "Identity" USA
        site_operator = " OR ".join([f"site:{d}" for d in chunk])
        
        # NOTE: We removed quotes around {term} to allow fuzzy matching
        query = f"({site_operator}) {term} {country_name}"
        
        try:
            # INCREASED LIMIT TO 10
            res = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, num=10).execute()
            for item in res.get('items', []):
                title = item['title'].split("|")[0].split("-")[0].strip()
                results.append({
                    'Title': title,
                    'Company': item['displayLink'].replace("www.", "").replace("careers.", ""), 
                    'Location': country_name, 
                    'Salary': 'Check Site',
                    'Description': item['snippet'],
                    'URL': item['link'],
                    'Source': 'Tier 1 Direct'
                })
        except: pass
        
    return results

def run_hybrid_search(criteria):
    all_results = []
    seen_urls = set()
    
    target_countries = criteria.get('countries', ['us'])[:3]
    
    # KEY CHANGE: We use different keywords for different engines
    specific_keywords = criteria.get('specific_keywords', [])[:2]
    broad_keywords = criteria.get('broad_keywords', [])[:2] # Broader terms for Tier 1
    
    progress = st.empty()
    
    for country in target_countries:
        c_code = COUNTRY_MAP.get(country.lower(), country.lower())
        
        # 1. Tier 1 Search (Using Broad Terms)
        # We run this FIRST to prioritize these results
        for term in broad_keywords:
            progress.text(f"💎 Tier 1 X-Ray: Scanning big tech for '{term}' in {country}...")
            jobs_t1 = search_tier_1_google(term, country)
            for j in jobs_t1:
                if j['URL'] not in seen_urls:
                    seen_urls.add(j['URL'])
                    all_results.append(j)

        # 2. Adzuna Search (Using Specific Terms)
        for term in specific_keywords:
            progress.text(f"🔍 Adzuna: Aggregating '{term}' in {country}...")
            jobs_adz = search_adzuna(term, c_code)
            for j in jobs_adz:
                if j['URL'] not in seen_urls:
                    seen_urls.add(j['URL'])
                    all_results.append(j)
    
    progress.empty()
    return all_results

# --- EMAIL ---
def send_jobs_email(user_email, df):
    msg = MIMEMultipart()
    msg['Subject'] = f"Job Results: {len(df)} Matches"
    msg['From'] = GMAIL_USER
    msg['To'] = user_email
    
    html = df[['Match %', 'Title', 'Company', 'Source', 'Location']].to_html(index=False)
    msg.attach(MIMEText(f"<h3>Job Report</h3>{html}", 'html'))
    
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    part = MIMEApplication(excel_buffer.getvalue(), Name="Jobs.xlsx")
    part['Content-Disposition'] = 'attachment; filename="Jobs.xlsx"'
    msg.attach(part)
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        return True
    except: return False

# --- UI ---
st.set_page_config(page_title="Global Hybrid Hunter", page_icon="🌐", layout="wide")
st.title("🌐 Global Hybrid Hunter V5")

with st.form("job_form"):
    c1, c2 = st.columns([1, 1])
    with c1:
        dream_description = st.text_area("Search Criteria", height=150, 
            value="Senior Infrastructure Architect. Active Directory Migration. Remote/Travel. $130k+")
        uploaded_resume = st.file_uploader("Upload CV (PDF)", type=["pdf"])
    with c2:
        user_email = st.text_input("Email Results To", "judd@sharphuman.com")

    submitted = st.form_submit_button("Run Search")

if submitted:
    resume_text = extract_text_from_pdf(uploaded_resume) if uploaded_resume else ""
    status = st.status("Initializing...", expanded=True)
    
    # 1. Plan
    criteria = parse_user_intent(dream_description, resume_text)
    
    if criteria:
        status.write(f"🗺️ Countries: **{criteria['countries']}**")
        status.write(f"🎯 Adzuna Keywords: **{criteria['specific_keywords']}**")
        status.write(f"💎 Tier 1 Keywords: **{criteria['broad_keywords']}** (Broader for more hits)")
        
        # 2. Execute
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
            df = df[df['Match %'] > 40].sort_values(by='Match %', ascending=False).head(50)
            
            if not df.empty:
                send_jobs_email(user_email, df)
                status.update(label="✅ Done!", state="complete", expanded=False)
                st.success("Report Sent!")
                
                for _, row in df.iterrows():
                    with st.expander(f"{row['Match %']}% {row['Title']} @ {row['Company']}"):
                        st.write(f"**Source:** {row['Source']}")
                        st.write(f"**Reason:** {row['Reason']}")
                        st.markdown(f"[Apply Now]({row['URL']})")
            else:
                status.update(label="No high matches", state="error")
        else:
            status.update(label="No Jobs Found", state="error")
