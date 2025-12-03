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

# --- CONFIGURATION ---
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
ADZUNA_APP_ID = st.secrets["ADZUNA_APP_ID"]
ADZUNA_APP_KEY = st.secrets["ADZUNA_APP_KEY"]
GMAIL_USER = st.secrets["GMAIL_USER"]
GMAIL_APP_PASSWORD = st.secrets["GMAIL_APP_PASSWORD"]

client = OpenAI(api_key=OPENAI_API_KEY)

# --- AI FUNCTIONS ---

def parse_dream_job(user_input):
    """
    Translates natural language into structured API parameters.
    """
    prompt = f"""
    You are a Career Consultant. Extract search parameters from this user's dream job description.
    USER INPUT: "{user_input}"
    OUTPUT JSON keys: "keywords" (string), "location" (string or blank), "is_remote" (boolean), "salary_min" (integer).
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]
        )
        return json.loads(response.choices[0].message.content)
    except: return None

def ai_analyze_job(job, user_dream):
    """
    The 'Vibe Check'. Analyzes the specific job description against user desires.
    Extracts hidden fields like Travel and Estimates Salary.
    """
    # We use gpt-4o-mini for speed/cost since we run this on multiple jobs
    prompt = f"""
    You are a Talent Agent. Analyze this job posting against the User's Dream.
    
    USER DREAM: "{user_dream}"
    
    JOB DETAILS:
    Title: {job['Title']}
    Company: {job['Company']}
    Location: {job['Location']}
    Provided Salary: {job['Salary']}
    Description: {job['Description'][:2000]} (truncated)
    
    TASK:
    1. Match Score (0-100): How well does this fit the user's vibe/requirements?
    2. Salary Estimation: If 'Provided Salary' is 0 or missing, estimate the Annual Salary range (e.g. "$120k - $150k") based on Title/Location. If provided, keep it.
    3. Travel: Extract travel requirements (e.g. "None", "25%", "Occasional"). If not mentioned, say "Not specified".
    4. Remote Status: Confirm if it looks Remote, Hybrid, or Onsite.
    5. Reason: 1 short sentence why this matches (or doesn't).
    
    OUTPUT JSON keys: "score" (int), "salary_est" (str), "travel" (str), "remote_status" (str), "reason" (str).
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]
        )
        return json.loads(response.choices[0].message.content)
    except:
        return {"score": 0, "salary_est": "N/A", "travel": "Unknown", "remote_status": "Unknown", "reason": "Error"}

# --- SEARCH FUNCTIONS ---

def search_adzuna(criteria):
    results = []
    # Defaulting to US for breadth, you can make this an input if needed
    base_url = "http://api.adzuna.com/v1/api/jobs/us/search/1"
    
    params = {
        'app_id': ADZUNA_APP_ID, 'app_key': ADZUNA_APP_KEY,
        'results_per_page': 15, # Fetch more to allow for filtering
        'what': criteria['keywords'], 'where': criteria.get('location', ''),
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
                'Salary': item.get('salary_min', 'Not listed'), # Often missing in Adzuna
                'Description': item.get('description', ''),
                'URL': item.get('redirect_url'),
                'Source': 'Adzuna'
            })
    except: pass
    return results

def search_remotive(criteria):
    if not criteria['is_remote']: return []
    results = []
    try:
        resp = requests.get("https://remotive.com/api/remote-jobs", params={'search': criteria['keywords']})
        data = resp.json()
        for item in data.get('jobs', [])[:10]:
            # Clean HTML from description
            raw_desc = item.get('description', '')
            clean_desc = BeautifulSoup(raw_desc, "html.parser").get_text()[:2000]
            
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

# --- EMAIL FUNCTION ---

def send_jobs_email(user_email, df):
    msg = MIMEMultipart()
    msg['Subject'] = f"Your Dream Jobs: Top {len(df)} Picks"
    msg['From'] = GMAIL_USER
    msg['To'] = user_email
    
    # HTML Body
    cols = ['Match %', 'Title', 'Company', 'Salary Est.', 'Remote', 'Reason', 'URL']
    html_table = df[cols].to_html(index=False, render_links=True)
    
    body = f"""
    <h3>Dream Job Report</h3>
    <p>AI Analyzed {len(df)} jobs. Here are the top matches based on your criteria.</p>
    {html_table}
    """
    msg.attach(MIMEText(body, 'html'))
    
    # Attach Excel
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Dream Jobs')
    excel_data = excel_buffer.getvalue()
    
    part = MIMEApplication(excel_data, Name="Dream_Jobs.xlsx")
    part['Content-Disposition'] = 'attachment; filename="Dream_Jobs.xlsx"'
    msg.attach(part)
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        return True
    except Exception as e:
        st.error(f"Email Error: {e}")
        return False

# --- MAIN UI ---
st.set_page_config(page_title="Find My Dream Job", page_icon="🚀", layout="wide")
st.title("🚀 Find My Dream Job")
st.markdown("I will search job boards, read the descriptions, and **estimate missing salaries** and **travel requirements** for you.")

with st.form("job_form"):
    col1, col2 = st.columns([2, 1])
    with col1:
        dream_description = st.text_area("Describe your Dream Job (Be specific!)", 
            height=150, 
            placeholder="Example: I want a Senior Product Manager role in Fintech, fully remote or in NYC. Salary $160k+. I hate travel. I love startups.")
    with col2:
        user_email = st.text_input("Email Results To", "your@email.com")
        st.info("💡 The AI will prioritize your 'Vibe' (Startup vs Corp, Remote vs Hybrid).")
        
    submitted = st.form_submit_button("Find & Analyze Jobs")

if submitted and dream_description:
    status = st.status("AI Agent is working...", expanded=True)
    
    # 1. Parse User Intent
    status.write("🧠 Interpreting your dream...")
    criteria = parse_dream_job(dream_description)
    
    # 2. Fetch Raw Jobs
    status.write(f"🔎 Searching Adzuna & Remotive for **{criteria['keywords']}**...")
    raw_jobs = search_adzuna(criteria) + search_remotive(criteria)
    
    if raw_jobs:
        status.write(f"👀 AI is analyzing {len(raw_jobs)} job descriptions (Extracting Salary/Travel/Vibe)...")
        
        analyzed_jobs = []
        progress_bar = status.progress(0)
        
        for i, job in enumerate(raw_jobs):
            progress_bar.progress((i + 1) / len(raw_jobs))
            
            # THE MAGIC STEP: AI analyzes the job text
            analysis = ai_analyze_job(job, dream_description)
            
            job['Match %'] = analysis.get('score', 0)
            job['Salary Est.'] = analysis.get('salary_est', job['Salary'])
            job['Travel'] = analysis.get('travel', 'Unknown')
            job['Remote'] = analysis.get('remote_status', 'Unknown')
            job['Reason'] = analysis.get('reason', '')
            
            analyzed_jobs.append(job)
            
        # 3. Filter & Sort
        df = pd.DataFrame(analyzed_jobs)
        df = df[df['Match %'] > 50].sort_values(by='Match %', ascending=False).head(20)
        
        if not df.empty:
            status.write("📧 Generating Report...")
            if send_jobs_email(user_email, df):
                status.update(label="✅ Dream Jobs Sent!", state="complete", expanded=False)
                st.success(f"Check {user_email} for your list!")
                
                # Display in App
                for index, row in df.iterrows():
                    with st.expander(f"{row['Match %']}% Match: {row['Title']} @ {row['Company']}"):
                        c1, c2, c3 = st.columns(3)
                        c1.metric("💰 Salary", row['Salary Est.'])
                        c2.metric("✈️ Travel", row['Travel'])
                        c3.metric("🏠 Remote", row['Remote'])
                        st.write(f"**AI Reason:** {row['Reason']}")
                        st.markdown(f"**[Apply Now]({row['URL']})**")
            else:
                status.update(label="❌ Email Failed", state="error")
        else:
            status.update(label="⚠️ No High Matches", state="error")
            st.warning("Found jobs, but the AI decided none of them matched your 'Dream' criteria well enough.")
    else:
        status.update(label="❌ No Jobs Found", state="error")
        st.error("No jobs returned from APIs. Try broader keywords.")
