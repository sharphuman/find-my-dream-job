import streamlit as st
import requests
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from openai import OpenAI
import json

# --- CONFIGURATION ---
# Secrets must be set in Streamlit Dashboard!
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
ADZUNA_APP_ID = st.secrets["ADZUNA_APP_ID"]
ADZUNA_APP_KEY = st.secrets["ADZUNA_APP_KEY"]
GMAIL_USER = st.secrets["GMAIL_USER"]
GMAIL_APP_PASSWORD = st.secrets["GMAIL_APP_PASSWORD"]

client = OpenAI(api_key=OPENAI_API_KEY)

# --- LLM TRANSLATOR ---
def parse_dream_job(user_input):
    """
    Translates natural language into structured API parameters.
    """
    prompt = f"""
    You are a Career Consultant. Extract search parameters from this user's dream job description.
    
    USER INPUT: "{user_input}"
    
    OUTPUT JSON with these exact keys:
    - "keywords": (string) Main job title or skill (e.g. "Python Developer").
    - "location": (string) City or Country (e.g. "London", "USA"). If remote mentioned, leave blank.
    - "is_remote": (boolean) True if user wants remote.
    - "salary_min": (integer) Minimum salary if mentioned (otherwise 0).
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        st.error(f"AI Parsing Error: {e}")
        return None

# --- API SEARCH FUNCTIONS ---

def search_adzuna(criteria):
    """
    Searches Adzuna (General Aggregator like Indeed).
    """
    results = []
    # Adzuna requires country code (gb, us, in, etc). We default to US for this demo.
    country = "us" 
    
    base_url = f"http://api.adzuna.com/v1/api/jobs/{country}/search/1"
    
    params = {
        'app_id': ADZUNA_APP_ID,
        'app_key': ADZUNA_APP_KEY,
        'results_per_page': 10,
        'what': criteria['keywords'],
        'where': criteria.get('location', ''),
        'content-type': 'application/json'
    }
    
    if criteria['salary_min'] > 0:
        params['salary_min'] = criteria['salary_min']

    try:
        resp = requests.get(base_url, params=params)
        data = resp.json()
        
        for item in data.get('results', []):
            results.append({
                'Title': item.get('title'),
                'Company': item.get('company', {}).get('display_name'),
                'Location': item.get('location', {}).get('display_name'),
                'Salary': f"${item.get('salary_min', 0)}",
                'URL': item.get('redirect_url'),
                'Source': 'Adzuna'
            })
    except Exception as e:
        print(f"Adzuna Error: {e}")
        
    return results

def search_remotive(criteria):
    """
    Searches Remotive (Best for Tech/Remote).
    Only runs if 'remote' or 'tech' keywords imply it.
    """
    if not criteria['is_remote']:
        return []

    results = []
    url = "https://remotive.com/api/remote-jobs"
    try:
        resp = requests.get(url, params={'search': criteria['keywords']})
        data = resp.json()
        
        # Limit to top 5 from Remotive to keep it fast
        for item in data.get('jobs', [])[:5]:
            results.append({
                'Title': item.get('title'),
                'Company': item.get('company_name'),
                'Location': item.get('candidate_required_location', 'Remote'),
                'Salary': item.get('salary', 'Not listed'),
                'URL': item.get('url'),
                'Source': 'Remotive'
            })
    except Exception as e:
        print(f"Remotive Error: {e}")
        
    return results

# --- EMAIL FUNCTION ---
def send_jobs_email(user_email, df):
    msg = MIMEMultipart()
    msg['Subject'] = f"Dream Jobs Found: Top {len(df)} Picks"
    msg['From'] = GMAIL_USER
    msg['To'] = user_email
    
    # Simple HTML Table
    html_table = df[['Title', 'Company', 'Salary', 'URL']].to_html(index=False, render_links=True)
    
    body = f"""
    <h3>Here are your top job matches:</h3>
    <p>Based on your dream job description.</p>
    {html_table}
    """
    msg.attach(MIMEText(body, 'html'))
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        st.error(f"Email Failed: {e}")
        return False

# --- MAIN APP UI ---
st.set_page_config(page_title="Find My Dream Job", page_icon="🚀", layout="wide")

st.title("🚀 Find My Dream Job")
st.markdown("Tell me about your perfect role (e.g. *'I want a marketing leadership role in Austin paying over $120k with a fun culture'*). I'll use AI to find matches.")

with st.form("job_form"):
    col1, col2 = st.columns([2, 1])
    
    with col1:
        dream_description = st.text_area("Describe your Dream Job", height=150)
    
    with col2:
        user_email = st.text_input("Email Results To", "your@email.com")
        # Optional manual overrides
        st.caption("Optional Filters (AI usually catches these)")
        manual_loc = st.text_input("Specific Location (Optional)")
        
    submitted = st.form_submit_button("Find Jobs")

if submitted and dream_description:
    status = st.status("AI Agent is searching...", expanded=True)
    
    # 1. Parse with AI
    status.write("🧠 Analyzing your requirements...")
    criteria = parse_dream_job(dream_description)
    
    if manual_loc: # Override if user manually typed it
        criteria['location'] = manual_loc
        
    status.write(f"🔎 Searching for: **{criteria['keywords']}** in **{criteria.get('location', 'Global')}**")
    
    # 2. Call APIs
    jobs_adzuna = search_adzuna(criteria)
    jobs_remotive = search_remotive(criteria)
    
    # 3. Combine Results
    all_jobs = jobs_adzuna + jobs_remotive
    
    if all_jobs:
        df = pd.DataFrame(all_jobs)
        
        # Keep top 20
        df = df.head(20)
        
        status.write("📧 Sending email...")
        if send_jobs_email(user_email, df):
            status.update(label="✅ Jobs Found & Sent!", state="complete", expanded=False)
            st.success(f"Sent {len(df)} jobs to {user_email}")
            
            # Display nicely in UI
            for index, row in df.iterrows():
                with st.expander(f"{row['Title']} at {row['Company']}"):
                    st.write(f"**Location:** {row['Location']}")
                    st.write(f"**Salary:** {row['Salary']}")
                    st.markdown(f"[Apply Now]({row['URL']})")
        else:
            status.update(label="⚠️ Email Failed", state="error")
            st.dataframe(df)
            
    else:
        status.update(label="❌ No Jobs Found", state="error")
        st.warning("No jobs found. Try broadening your description (e.g. remove salary constraints).")
