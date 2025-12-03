import streamlit as st
import requests
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from openai import OpenAI
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

client = OpenAI(api_key=OPENAI_API_KEY)

# --- CONFIG: COUNTRY MAPPING ---
# If AI detects "EU", we expand it to these tech hubs
EU_EXPANSION = ["gb", "de", "fr", "nl", "it", "es"]
COUNTRY_MAP = {
    "usa": "us", "us": "us", "united states": "us",
    "australia": "au", "oz": "au", "au": "au",
    "uk": "gb", "britain": "gb", "england": "gb",
    "canada": "ca", "germany": "de", "france": "fr",
    "netherlands": "nl", "india": "in"
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
    Extracts MULTIPLE countries and MULTIPLE search terms to create a broader net.
    """
    prompt = f"""
    You are a Global Headhunter. Plan a search strategy.
    
    USER DREAM: "{dream_desc}"
    USER RESUME: "{resume_text[:2000]}"
    
    TASK:
    1. Keywords: Generate 3 DISTINCT boolean search phrases (e.g. "Senior Systems Engineer", "Active Directory Architect", "Windows Tech Lead").
    2. Countries: Identify target country codes (us, gb, au, ca, de, fr, nl, in, za). If user says "EU", include 'de', 'fr', 'nl'.
    3. Remote: Boolean.
    
    OUTPUT JSON:
    {{
        "keywords": ["term1", "term2", "term3"],
        "countries": ["us", "au", ...],
        "is_remote": true/false
    }}
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
    Rate this job for the user.
    
    USER WANTS: "{dream_desc}"
    USER SKILLS: "{resume_text[:1000]}"
    
    JOB: {job['Title']} @ {job['Company']} in {job['Location']}
    DESC: {job['Description'][:1000]}
    
    TASK:
    1. Score (0-100).
    2. Estimate Salary (e.g. "$120k").
    3. Extract Travel/Remote/Visa logic.
    
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

# --- SEARCH ENGINE (GLOBAL) ---

def search_adzuna(term, country):
    """
    Searches a specific keyword in a specific country.
    """
    results = []
    base_url = f"http://api.adzuna.com/v1/api/jobs/{country}/search/1"
    
    params = {
        'app_id': ADZUNA_APP_ID, 'app_key': ADZUNA_APP_KEY,
        'results_per_page': 10,
        'what': term, 
        'sort_by': 'date', # <--- CRITICAL: Get fresh jobs, not just "relevant" ones
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
                'Source': f'Adzuna-{country.upper()}'
            })
    except: pass
    return results

def run_global_search(criteria):
    all_results = []
    seen_urls = set()
    
    # LIMITS: To prevent API timeouts, we limit combinations
    # 3 Keywords x 3 Countries = 9 API Calls.
    target_countries = criteria.get('countries', ['us'])[:4] 
    target_keywords = criteria.get('keywords', [])[:3]
    
    for country in target_countries:
        # Normalize country code
        c_code = COUNTRY_MAP.get(country.lower(), country.lower())
        if c_code not in COUNTRY_MAP.values(): continue # Skip invalid codes
        
        for term in target_keywords:
            jobs = search_adzuna(term, c_code)
            
            # Deduplicate
            for j in jobs:
                if j['URL'] not in seen_urls:
                    seen_urls.add(j['URL'])
                    all_results.append(j)
                    
    return all_results

# --- EMAIL ---
def send_jobs_email(user_email, df):
    msg = MIMEMultipart()
    msg['Subject'] = f"Global Job Search Results ({len(df)})"
    msg['From'] = GMAIL_USER
    msg['To'] = user_email
    
    html = df[['Match %', 'Title', 'Company', 'Location', 'Salary Est.']].to_html(index=False)
    msg.attach(MIMEText(f"<h3>Job Matches</h3>{html}", 'html'))
    
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    part = MIMEApplication(excel_buffer.getvalue(), Name="Global_Jobs.xlsx")
    part['Content-Disposition'] = 'attachment; filename="Global_Jobs.xlsx"'
    msg.attach(part)
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        return True
    except: return False

# --- UI ---
st.set_page_config(page_title="Global Dream Job", page_icon="🌍", layout="wide")
st.title("🌍 Global Dream Job Finder")
st.markdown("I search **across borders** using variations of your job title to find the best matches.")

with st.form("job_form"):
    c1, c2 = st.columns([1, 1])
    with c1:
        dream_description = st.text_area("What do you want?", height=150, 
            value="Senior Systems Engineer or Tech Lead. Windows & Active Directory. Remote with travel. $100k+. I have passports for USA, EU, and Australia.")
        uploaded_resume = st.file_uploader("Upload CV (PDF)", type=["pdf"])
    with c2:
        user_email = st.text_input("Email Results To", "judd@sharphuman.com")
        st.info("ℹ️ I will auto-detect your target countries (USA, EU, AU) from your text.")

    submitted = st.form_submit_button("Run Global Search")

if submitted:
    # 1. Parse Resume
    resume_text = ""
    if uploaded_resume:
        resume_text = extract_text_from_pdf(uploaded_resume)

    status = st.status("Initializing Global Agent...", expanded=True)
    
    # 2. Plan Strategy
    status.write("🧠 Planning search strategy...")
    criteria = parse_user_intent(dream_description, resume_text)
    
    if criteria:
        kw_list = criteria['keywords']
        ct_list = criteria['countries']
        
        status.write(f"🗺️ Target Countries: **{ct_list}**")
        status.write(f"🔑 Search Terms: **{kw_list}**")
        
        # 3. Execute Search
        status.write(f"🚀 Running {len(kw_list) * len(ct_list)} search combinations (Sorted by Freshness)...")
        raw_jobs = run_global_search(criteria)
        
        if raw_jobs:
            status.write(f"👀 Analyzing {len(raw_jobs)} candidates...")
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
            df = df[df['Match %'] > 50].sort_values(by='Match %', ascending=False).head(25)
            
            if not df.empty:
                send_jobs_email(user_email, df)
                status.update(label="✅ Done!", state="complete", expanded=False)
                st.success("Sent to Email!")
                
                for _, row in df.iterrows():
                    with st.expander(f"{row['Match %']}% {row['Title']} ({row['Location']})"):
                        st.write(f"**Reason:** {row['Reason']}")
                        st.write(f"**Salary:** {row['Salary Est.']}")
                        st.markdown(f"[Apply Now]({row['URL']})")
            else:
                status.update(label="No high matches", state="error")
                st.warning("Found jobs, but AI filtered them out based on your preferences.")
        else:
            status.update(label="No Jobs Found", state="error")
            st.error("0 Jobs found across all countries.")
