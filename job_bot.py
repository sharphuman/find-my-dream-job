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
import random  # <--- NEW: To pick random companies

# --- CONFIGURATION ---
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
ADZUNA_APP_ID = st.secrets["ADZUNA_APP_ID"]
ADZUNA_APP_KEY = st.secrets["ADZUNA_APP_KEY"]
GMAIL_USER = st.secrets["GMAIL_USER"]
GMAIL_APP_PASSWORD = st.secrets["GMAIL_APP_PASSWORD"]
GOOGLE_API_KEY = st.secrets.get("GOOGLE_API_KEY")
SEARCH_ENGINE_ID = st.secrets.get("SEARCH_ENGINE_ID")

client = OpenAI(api_key=OPENAI_API_KEY)

# --- NON-TECH ENTERPRISE TARGETS (The "Hidden Gem" List) ---
# Banks, Pharma, Auto, Energy, Retail
ENTERPRISE_DOMAINS = [
    # Finance & Banking (Huge AD Environments)
    "jpmorganchase.com/careers", "careers.bankofamerica.com", "wellsfargo.com/careers",
    "careers.citigroup.com", "goldmansachs.com/careers", "morganstanley.com/careers",
    "americanexpress.com/careers", "capitalone.com/careers",
    
    # Healthcare & Pharma (Complex Compliance/Identity needs)
    "careers.unitedhealthgroup.com", "jobs.cvshealth.com", "pfizer.com/careers",
    "careers.jnj.com", "merck.com/careers", "bms.com/careers",
    
    # Retail & Consumer (Massive Scale)
    "careers.walmart.com", "careers.homedepot.com", "corporate.target.com/careers",
    "careers.pepsico.com", "coca-colacompany.com/careers", "pgcareers.com",
    
    # Industrial, Auto & Energy
    "jobs.generalmotors.com", "careers.ford.com", "jobs.gecareers.com",
    "boeing.com/careers", "lockheedmartinjobs.com", "careers.exxonmobil.com",
    "careers.chevron.com", "shell.com/careers",
    
    # Telecom
    "verizon.com/about/careers", "att.jobs", "t-mobile.com/careers"
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
    prompt = f"""
    You are a Headhunter. Plan a search strategy.
    
    USER DREAM: "{dream_desc}"
    USER RESUME: "{resume_text[:2000]}"
    
    TASK:
    1. specific_keywords: 2 specific boolean phrases for Adzuna (e.g. "Active Directory Architect").
    2. broad_keywords: 2 broad terms for Corporate Career Sites (e.g. "Identity Manager", "Infrastructure").
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
        'results_per_page': 10, 'what': term, 'sort_by': 'date',
        'max_days_old': 30, 'content-type': 'application/json'
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

def search_enterprise_google(term, country_name):
    """
    X-Ray Search against Non-Tech Enterprise Sites
    """
    if not GOOGLE_API_KEY: return []
    
    service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
    results = []
    
    # RANDOMIZE: Pick 15 random companies from our list of 30+ to keep it fresh
    target_domains = random.sample(ENTERPRISE_DOMAINS, min(15, len(ENTERPRISE_DOMAINS)))
    
    # Chunk domains
    domain_chunks = [target_domains[i:i + 5] for i in range(0, len(target_domains), 5)]
    
    for chunk in domain_chunks:
        # Query: (site:jpmorgan.com OR site:pfizer.com) "Active Directory" USA
        site_operator = " OR ".join([f"site:{d}" for d in chunk])
        query = f"({site_operator}) {term} {country_name}"
        
        try:
            # Fetch 10 results per chunk
            res = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, num=10).execute()
            for item in res.get('items', []):
                title = item['title'].split("|")[0].split("-")[0].strip()
                results.append({
                    'Title': title,
                    'Company': item['displayLink'].replace("www.", "").replace("careers.", "").replace(".com", ""), 
                    'Location': country_name, 
                    'Salary': 'Check Site',
                    'Description': item['snippet'],
                    'URL': item['link'],
                    'Source': 'Enterprise Direct'
                })
        except: pass
        
    return results

def run_hybrid_search(criteria):
    all_results = []
    seen_urls = set()
    
    target_countries = criteria.get('countries', ['us'])[:3]
    specific_keywords = criteria.get('specific_keywords', [])[:2]
    broad_keywords = criteria.get('broad_keywords', [])[:2]
    
    progress = st.empty()
    
    for country in target_countries:
        c_code = COUNTRY_MAP.get(country.lower(), country.lower())
        
        # 1. Enterprise X-Ray (Broad Terms)
        for term in broad_keywords:
            progress.text(f"🏢 Scanning Fortune 500 for '{term}' in {country}...")
            jobs_ent = search_enterprise_google(term, country)
            for j in jobs_ent:
                if j['URL'] not in seen_urls:
                    seen_urls.add(j['URL'])
                    all_results.append(j)

        # 2. Adzuna (Specific Terms)
        for term in specific_keywords:
            progress.text(f"🔍 Adzuna: '{term}' in {country}...")
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
    msg['Subject'] = f"Enterprise Job Matches ({len(df)})"
    msg['From'] = GMAIL_USER
    msg['To'] = user_email
    
    html = df[['Match %', 'Title', 'Company', 'Source', 'Location']].to_html(index=False)
    msg.attach(MIMEText(f"<h3>Enterprise Job Report</h3>{html}", 'html'))
    
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    part = MIMEApplication(excel_buffer.getvalue(), Name="Enterprise_Jobs.xlsx")
    part['Content-Disposition'] = 'attachment; filename="Enterprise_Jobs.xlsx"'
    msg.attach(part)
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        return True
    except: return False

# --- UI ---
st.set_page_config(page_title="Enterprise Hunter", page_icon="🏢", layout="wide")
st.title("🏢 Non-Tech Enterprise Hunter")
st.markdown("I search **Fortune 500 Banks, Pharma, & Retail** giants for Infrastructure roles.")

with st.form("job_form"):
    c1, c2 = st.columns([1, 1])
    with c1:
        dream_description = st.text_area("Search Criteria", height=150, 
            value="Senior Infrastructure Architect. Active Directory Migration. Remote/Travel. $130k+")
        uploaded_resume = st.file_uploader("Upload CV (PDF)", type=["pdf"])
    with c2:
        user_email = st.text_input("Email Results To", "judd@sharphuman.com")
        st.info("ℹ️ Targets: JP Morgan, Pfizer, Walmart, Ford, Boeing, etc.")

    submitted = st.form_submit_button("Run Search")

if submitted:
    resume_text = extract_text_from_pdf(uploaded_resume) if uploaded_resume else ""
    status = st.status("Initializing...", expanded=True)
    
    criteria = parse_user_intent(dream_description, resume_text)
    
    if criteria:
        status.write(f"🗺️ Targets: **{criteria['countries']}**")
        status.write(f"🔑 Keywords: **{criteria['broad_keywords']}**")
        
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
