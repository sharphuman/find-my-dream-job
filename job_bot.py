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
from pypdf import PdfReader # <--- NEW LIBRARY

# --- CONFIGURATION ---
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
ADZUNA_APP_ID = st.secrets["ADZUNA_APP_ID"]
ADZUNA_APP_KEY = st.secrets["ADZUNA_APP_KEY"]
GMAIL_USER = st.secrets["GMAIL_USER"]
GMAIL_APP_PASSWORD = st.secrets["GMAIL_APP_PASSWORD"]

client = OpenAI(api_key=OPENAI_API_KEY)

# --- HELPER: PDF READER ---
def extract_text_from_pdf(uploaded_file):
    try:
        reader = PdfReader(uploaded_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        return text[:5000] # Truncate to avoid huge API costs
    except Exception as e:
        st.error(f"Error reading PDF: {e}")
        return ""

# --- AI FUNCTIONS ---

def parse_user_intent(dream_desc, resume_text):
    """
    Combines User Dreams + Resume Skills to create the perfect search query.
    """
    prompt = f"""
    You are a Career Agent.
    
    USER'S DREAM DESCRIPTION: "{dream_desc}"
    USER'S RESUME (Excerpt): "{resume_text[:2000]}"
    
    TASK:
    Combine the user's explicit desires (Dream) with their actual hard skills (Resume) to create the best job search parameters.
    
    OUTPUT JSON keys: 
    - "keywords" (string: specific title or skills), 
    - "location" (string or blank), 
    - "is_remote" (boolean), 
    - "salary_min" (integer).
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
    The Vibe Check: Compares Job vs Dream + Resume.
    """
    prompt = f"""
    You are a Talent Agent. Rate this job for my client.
    
    CLIENT DREAM: "{dream_desc}"
    CLIENT SKILLS (RESUME): "{resume_text[:1000]}"
    
    JOB POSTING:
    Title: {job['Title']}
    Company: {job['Company']}
    Loc: {job['Location']}
    Salary: {job['Salary']}
    Desc: {job['Description'][:1500]}
    
    TASK:
    1. Score (0-100): Does it match their Dream AND their Skills?
    2. Estimate Salary: If missing, guess based on market.
    3. Extract Travel/Remote details.
    
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
        return {"score": 0, "salary_est": "N/A", "travel": "?", "remote_status": "?", "reason": "Error"}

# --- SEARCH FUNCTIONS (ADZUNA & REMOTIVE) ---

def search_adzuna(criteria):
    results = []
    # Default US, but AI 'location' field can override if we built logic for it. 
    # For now, we search US database but filter by specific location in params.
    base_url = "http://api.adzuna.com/v1/api/jobs/us/search/1"
    
    params = {
        'app_id': ADZUNA_APP_ID, 'app_key': ADZUNA_APP_KEY,
        'results_per_page': 15,
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
                'Salary': item.get('salary_min', 'Not listed'),
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

# --- EMAIL FUNCTION ---

def send_jobs_email(user_email, df):
    msg = MIMEMultipart()
    msg['Subject'] = f"Dream Jobs Match Report"
    msg['From'] = GMAIL_USER
    msg['To'] = user_email
    
    cols = ['Match %', 'Title', 'Company', 'Salary Est.', 'Remote', 'Reason', 'URL']
    html_table = df[cols].to_html(index=False, render_links=True)
    
    body = f"""
    <h3>Dream Job Report</h3>
    <p>We analyzed jobs against your <strong>Resume</strong> and <strong>Description</strong>.</p>
    {html_table}
    """
    msg.attach(MIMEText(body, 'html'))
    
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Matches')
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
st.title("🚀 Find My Dream Job (CV Powered)")
st.markdown("Upload your CV and tell me what you want. I'll find jobs that match your **Skills** AND your **Vibe**.")

with st.form("job_form"):
    col1, col2 = st.columns([1, 1])
    
    with col1:
        dream_description = st.text_area("Describe your Dream Job", height=150, 
            placeholder="e.g. I want a remote Product Manager role in Tech. I hate micromanagement.")
        uploaded_resume = st.file_uploader("Upload your CV (PDF)", type=["pdf"])
        
    with col2:
        user_email = st.text_input("Email Results To", "your@email.com")
        st.info("💡 Uploading your CV helps the AI match specific skills (e.g. 'Python', 'Agile') that you might forget to type.")
        
    submitted = st.form_submit_button("Find Matches")

if submitted and dream_description:
    status = st.status("Agent is working...", expanded=True)
    
    # 1. Parse Resume
    resume_text = ""
    if uploaded_resume:
        status.write("📄 Reading your CV...")
        resume_text = extract_text_from_pdf(uploaded_resume)
    
    # 2. Parse Intent (Dream + Resume)
    status.write("🧠 Combining your Dream + Resume Skills...")
    criteria = parse_user_intent(dream_description, resume_text)
    
    # 3. Search
    status.write(f"🔎 Searching for: **{criteria['keywords']}**...")
    raw_jobs = search_adzuna(criteria) + search_remotive(criteria)
    
    if raw_jobs:
        status.write(f"👀 Analyzing {len(raw_jobs)} jobs against your profile...")
        
        analyzed_jobs = []
        progress_bar = status.progress(0)
        
        for i, job in enumerate(raw_jobs):
            progress_bar.progress((i + 1) / len(raw_jobs))
            # AI Check
            analysis = ai_analyze_job(job, dream_description, resume_text)
            
            job['Match %'] = analysis.get('score', 0)
            job['Salary Est.'] = analysis.get('salary_est', job['Salary'])
            job['Travel'] = analysis.get('travel', 'Unknown')
            job['Remote'] = analysis.get('remote_status', 'Unknown')
            job['Reason'] = analysis.get('reason', '')
            
            analyzed_jobs.append(job)
            
        df = pd.DataFrame(analyzed_jobs)
        df = df[df['Match %'] > 40].sort_values(by='Match %', ascending=False).head(20)
        
        if not df.empty:
            status.write("📧 Sending Report...")
            if send_jobs_email(user_email, df):
                status.update(label="✅ Sent!", state="complete", expanded=False)
                st.success(f"Report sent to {user_email}")
                
                for index, row in df.iterrows():
                    with st.expander(f"{row['Match %']}% Match: {row['Title']} @ {row['Company']}"):
                        st.write(f"**Reason:** {row['Reason']}")
                        st.write(f"**Salary:** {row['Salary Est.']}")
                        st.markdown(f"[Apply Now]({row['URL']})")
            else:
                status.update(label="❌ Email Failed", state="error")
        else:
            status.update(label="⚠️ No matches found", state="error")
            st.warning("Jobs found, but none matched your resume/dream well enough.")
    else:
        status.update(label="❌ No Jobs Found", state="error")
