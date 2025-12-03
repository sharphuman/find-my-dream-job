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
import pdfplumber  # <--- The "Smart" PDF Reader

# --- CONFIGURATION ---
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
ADZUNA_APP_ID = st.secrets["ADZUNA_APP_ID"]
ADZUNA_APP_KEY = st.secrets["ADZUNA_APP_KEY"]
GMAIL_USER = st.secrets["GMAIL_USER"]
GMAIL_APP_PASSWORD = st.secrets["GMAIL_APP_PASSWORD"]

client = OpenAI(api_key=OPENAI_API_KEY)

# --- ADZUNA COUNTRY CODES ---
# Adzuna requires specific codes. We map them here.
ADZUNA_COUNTRIES = {
    "United States": "us",
    "United Kingdom": "gb",
    "Canada": "ca",
    "Australia": "au",
    "Germany": "de",
    "France": "fr",
    "India": "in",
    "Netherlands": "nl",
    "South Africa": "za"
}

# --- HELPER: ROBUST PDF READER ---
def extract_text_from_pdf(uploaded_file):
    """
    Uses pdfplumber to read the PDF. It handles columns better than pypdf.
    """
    text = ""
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            for page in pdf.pages:
                # Extract text preserving layout density
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text[:5000] # Limit to avoid token limits
    except Exception as e:
        st.error(f"Error reading PDF: {e}")
        return ""

# --- AI FUNCTIONS ---

def parse_user_intent(dream_desc, resume_text):
    prompt = f"""
    You are a Career Agent.
    
    USER'S DREAM: "{dream_desc}"
    USER'S RESUME: "{resume_text[:2500]}"
    
    TASK:
    Combine the user's desires (Dream) with their proven skills (Resume) to create search parameters.
    
    OUTPUT JSON keys: 
    - "keywords": (string) The best 2-3 keywords for a job search (e.g. "Senior Python Developer").
    - "is_remote": (boolean)
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
    # Quick analysis with mini model
    prompt = f"""
    Compare this Job to the User's Resume & Dream.
    
    USER DREAM: "{dream_desc}"
    RESUME SKILLS: "{resume_text[:1000]}"
    
    JOB:
    Title: {job['Title']}
    Desc: {job['Description'][:1500]}
    
    TASK:
    1. Score (0-100): Match %?
    2. Estimate Salary: If missing, estimate based on Title.
    
    OUTPUT JSON keys: "score" (int), "salary_est" (str), "reason" (str).
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

# --- SEARCH FUNCTIONS ---

def search_adzuna(criteria, country_code):
    results = []
    # Dynamic URL based on country code
    base_url = f"http://api.adzuna.com/v1/api/jobs/{country_code}/search/1"
    
    params = {
        'app_id': ADZUNA_APP_ID, 'app_key': ADZUNA_APP_KEY,
        'results_per_page': 20,
        'what': criteria['keywords'], 
        'content-type': 'application/json'
    }
    try:
        resp = requests.get(base_url, params=params)
        data = resp.json()
        for item in data.get('results', []):
            results.append({
                'Title': item.get('title'),
                'Company': item.get('company', {}).get('display_name'),
                'Location': item.get('location', {}).get('display_name'),
                'Salary': item.get('salary_min', 'Not listed'),
                'Description': item.get('description', ''),
                'URL': item.get('redirect_url'),
                'Source': 'Adzuna'
            })
    except Exception as e:
        print(f"Adzuna Error: {e}")
    return results

def search_remotive(criteria):
    if not criteria['is_remote']: return []
    results = []
    try:
        resp = requests.get("https://remotive.com/api/remote-jobs", params={'search': criteria['keywords']})
        data = resp.json()
        for item in data.get('jobs', [])[:10]:
            clean_desc = BeautifulSoup(item.get('description', ''), "html.parser").get_text()[:2000]
            results.append({
                'Title': item.get('title'),
                'Company': item.get('company_name'),
                'Location': item.get('candidate_required_location', 'Remote'),
                'Salary': item.get('salary', 'Not listed'),
                'Description': clean_desc,
                'URL': item.get('url'),
                'Source': 'Remotive'
            })
    except: pass
    return results

# --- EMAIL ---
def send_jobs_email(user_email, df):
    msg = MIMEMultipart()
    msg['Subject'] = f"Job Matches (Top {len(df)})"
    msg['From'] = GMAIL_USER
    msg['To'] = user_email
    
    html = df[['Match %', 'Title', 'Company', 'Salary Est.', 'URL']].to_html(index=False, render_links=True)
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
st.set_page_config(page_title="Find My Dream Job", page_icon="🚀", layout="wide")
st.title("🚀 Find My Dream Job (V2)")

with st.form("job_form"):
    c1, c2 = st.columns([1, 1])
    with c1:
        # NEW: Country Selector
        country_name = st.selectbox("Search in Country", list(ADZUNA_COUNTRIES.keys()), index=0)
        dream_description = st.text_area("Dream Job Description", height=150, placeholder="e.g. Remote DevOps role")
        uploaded_resume = st.file_uploader("Upload CV (PDF)", type=["pdf"])
    with c2:
        user_email = st.text_input("Email Results To", "your@email.com")
        
    submitted = st.form_submit_button("Find Matches")

if submitted:
    # 1. Debugging Info
    debug_tab, results_tab = st.tabs(["🛠️ Debugger (See what AI sees)", "✅ Results"])
    
    resume_text = ""
    if uploaded_resume:
        resume_text = extract_text_from_pdf(uploaded_resume)
        with debug_tab:
            st.warning("Raw Resume Text Extracted (Check this if results are bad):")
            st.text(resume_text[:1000] + "...") # Show first 1000 chars
            
    if dream_description:
        status = st.status("Searching...", expanded=True)
        
        # 2. Parse
        criteria = parse_user_intent(dream_description, resume_text)
        country_code = ADZUNA_COUNTRIES[country_name]
        
        with debug_tab:
            st.info(f"AI Search Keywords: **{criteria['keywords']}**")
            st.info(f"Targeting Country: **{country_code.upper()}**")

        # 3. Search
        status.write(f"Searching {country_name}...")
        jobs = search_adzuna(criteria, country_code) + search_remotive(criteria)
        
        if jobs:
            status.write(f"Analyzing {len(jobs)} jobs...")
            analyzed = []
            
            for j in jobs:
                a = ai_analyze_job(j, dream_description, resume_text)
                j['Match %'] = a.get('score', 0)
                j['Salary Est.'] = a.get('salary_est', j['Salary'])
                j['Reason'] = a.get('reason', '')
                analyzed.append(j)
                
            df = pd.DataFrame(analyzed)
            df = df[df['Match %'] > 40].sort_values(by='Match %', ascending=False).head(20)
            
            if not df.empty:
                send_jobs_email(user_email, df)
                status.update(label="Done!", state="complete", expanded=False)
                
                with results_tab:
                    st.success(f"Found {len(df)} matches!")
                    for _, row in df.iterrows():
                        with st.expander(f"{row['Match %']}% {row['Title']} ({row['Company']})"):
                            st.write(f"**Reason:** {row['Reason']}")
                            st.write(f"**Est. Salary:** {row['Salary Est.']}")
                            st.markdown(f"[Apply Here]({row['URL']})")
            else:
                status.update(label="No high matches", state="error")
                st.error("Jobs found, but AI filtered them all out as low match.")
        else:
            status.update(label="No Jobs Found", state="error")
            st.error(f"Adzuna/Remotive returned 0 results for '{criteria['keywords']}' in {country_code}.")
