# app.py
import streamlit as st
import requests
import io
import os
import json
from datetime import datetime
from docx import Document
from typing import Dict, Any, Optional

# ---------------------------
# CONFIG - change if needed
# ---------------------------
GITHUB_OWNER = "SaiThota97"
GITHUB_REPO = "Portfolio"
RESUME_PATH_IN_REPO = "SaiThota_Resume_TS.docx"
BRANCH = "main"  # change if your branch name differs
JSON_CACHE = "resume_data.json"

# Paths to local assets inside the repo (add these files to your repo)
PROFILE_IMG = "assets/profile.jpg"
COMPANY_LOGOS = {
    "Allied World Assurance Company (AWAC)": "assets/logos/awac.png",
    "McAfee": "assets/logos/mcafee.png",
    "GE HealthCare": "assets/logos/ge.png",
    "N-iX": "assets/logos/nix.png"
}
CERT_LOGOS = {
    "Databricks Generative AI Fundamentals": "assets/certs/databricks.png",
    "Microsoft Azure AI Engineer Associate": "assets/certs/azure.png",
    "AWS Cloud Practitioner": "assets/certs/aws.png"
}
EDU_LOGO = "assets/edu/usu.png"

# ---------------------------
# Helpers: GitHub API + raw download
# ---------------------------
def get_github_commits_for_file(owner: str, repo: str, path: str, branch: str = "main", token: Optional[str] = None) -> Optional[dict]:
    """
    Returns the most recent commit JSON for `path` or None on failure.
    Uses: GET /repos/{owner}/{repo}/commits?path={path}&sha={branch}&per_page=1
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/commits"
    params = {"path": path, "sha": branch, "per_page": 1}
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    r = requests.get(url, params=params, headers=headers, timeout=15)
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0]  # latest commit for that file
    return None

def download_raw_file(owner: str, repo: str, path: str, branch: str = "main", token: Optional[str] = None) -> Optional[bytes]:
    """
    Downloads the raw file bytes using raw.githubusercontent.com
    """
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    r = requests.get(raw_url, headers=headers, timeout=30)
    if r.status_code == 200:
        return r.content
    return None

# ---------------------------
# Parse .docx (tailored to your fixed-format doc)
# ---------------------------
def parse_resume_docx_bytes(docx_bytes: bytes) -> Dict[str, Any]:
    doc = Document(io.BytesIO(docx_bytes))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]

    parsed = {
        "name": "",
        "role": "",
        "contact_line": "",
        "summary": "",
        "skills": "",
        "experience": {},      # company -> text
        "certifications": [],
        "education": ""
    }

    # Basic header parsing (first few paragraphs)
    if len(paragraphs) >= 1:
        parsed["name"] = paragraphs[0]
    if len(paragraphs) >= 2:
        parsed["role"] = paragraphs[1]
    if len(paragraphs) >= 3:
        parsed["contact_line"] = paragraphs[2]

    # Section detection: we'll iterate paragraphs and switch sections
    section = None
    current_company = None
    company_keys = list(COMPANY_LOGOS.keys())  # expected company names (fixed)
    for txt in paragraphs[3:]:
        upper = txt.upper()
        if upper.startswith("PROFESSIONAL SUMMARY"):
            section = "summary"
            continue
        if upper == "SKILLS" or upper.startswith("SKILLS"):
            section = "skills"
            continue
        if upper.startswith("PROFESSIONAL EXPERIENCE"):
            section = "experience"
            continue
        if upper.startswith("CERTIFICATIONS"):
            section = "certifications"
            continue
        if upper.startswith("EDUCATION"):
            section = "education"
            continue

        # Experience: detect lines like "Client: Allied World Assurance Company (AWAC) | New York, NY"
        if section == "experience" and txt.startswith("Client:"):
            # extract company substring
            # e.g. "Client: Allied World Assurance Company (AWAC) | New York, NY"
            remainder = txt[len("Client:"):].strip()
            # find if any company name is present:
            matched = None
            for c in company_keys:
                if c in remainder:
                    matched = c
                    break
            if matched:
                current_company = matched
                parsed["experience"][current_company] = ""
            else:
                # fallback: use the whole remainder as company key
                current_company = remainder
                parsed["experience"][current_company] = ""
            continue

        # Append content to sections
        if section == "summary":
            parsed["summary"] += txt + "\n"
        elif section == "skills":
            parsed["skills"] += txt + "\n"
        elif section == "experience":
            if current_company:
                parsed["experience"][current_company] += txt + "\n"
        elif section == "certifications":
            # some lines may be bullet or single name
            parsed["certifications"].append(txt)
        elif section == "education":
            parsed["education"] += txt + "\n"
        else:
            # If we haven't found a section yet, ignore or append to summary
            parsed["summary"] += txt + "\n"

    return parsed

# ---------------------------
# Main logic: check commit date -> download & parse -> cache JSON
# ---------------------------
def load_resume_from_github(owner: str, repo: str, path: str, branch: str = "main", token: Optional[str] = None) -> Dict[str, Any]:
    # 1) find last commit for file
    commit = get_github_commits_for_file(owner, repo, path, branch, token)
    commit_iso = None
    if commit:
        # commit['commit']['committer']['date'] is an ISO timestamp
        commit_iso = commit.get("commit", {}).get("committer", {}).get("date") or commit.get("commit", {}).get("author", {}).get("date")

    # 2) if JSON cache exists and timestamps match -> load cached
    if os.path.exists(JSON_CACHE):
        try:
            with open(JSON_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if commit_iso and cached.get("last_updated") == commit_iso:
                return cached["content"]
        except Exception:
            pass  # fall through to re-download/parse

    # 3) download raw .docx bytes
    content_bytes = download_raw_file(owner, repo, path, branch, token)
    if content_bytes is None:
        # If we cannot download, but cached exists, return cached anyway
        if os.path.exists(JSON_CACHE):
            with open(JSON_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            return cached.get("content", {})
        raise RuntimeError("Could not download resume file from GitHub and no cache available.")

    # 4) parse docx into structured JSON
    parsed = parse_resume_docx_bytes(content_bytes)

    # 5) write JSON cache file with last_updated = commit_iso (or current time fallback)
    last_updated = commit_iso or datetime.utcnow().isoformat()
    with open(JSON_CACHE, "w", encoding="utf-8") as f:
        json.dump({"last_updated": last_updated, "content": parsed}, f, indent=2)

    return parsed

# ---------------------------
# Streamlit UI
# ---------------------------
def main():
    st.set_page_config(page_title="Sai Thota — Resume", layout="wide")
    st.title("")  # we use header elements below

    # Optional: allow user to provide GITHUB_TOKEN via Streamlit secrets to avoid rate limit
    token = None
    if st.secrets.get("GITHUB_TOKEN", None):
        token = st.secrets["GITHUB_TOKEN"]

    with st.spinner("Loading resume from GitHub..."):
        try:
            resume = load_resume_from_github(GITHUB_OWNER, GITHUB_REPO, RESUME_PATH_IN_REPO, BRANCH, token)
        except Exception as e:
            st.error(f"Error loading resume: {e}")
            st.stop()

    # Header layout: left profile image, center headline & summary
    col_left, col_center = st.columns([1, 3])
    with col_left:
        if os.path.exists(PROFILE_IMG):
            st.image(PROFILE_IMG, width=200)
        else:
            st.write("Profile image not found: " + PROFILE_IMG)

    with col_center:
        name = resume.get("name", "SAI THOTA")
        role = resume.get("role", "AI / Machine Learning Engineer")
        st.markdown(f"<h1 style='text-align:center'>{name}</h1>", unsafe_allow_html=True)
        st.markdown(f"<h3 style='text-align:center'>{role}</h3>", unsafe_allow_html=True)
        if resume.get("contact_line"):
            st.markdown(f"<p style='text-align:center'>{resume['contact_line']}</p>", unsafe_allow_html=True)
        st.markdown("---")
        st.header("Professional Summary")
        st.write(resume.get("summary", "").strip())

    st.markdown("---")
    st.subheader("Work Experience")

    # Company logos row (click to open modal)
    companies = list(COMPANY_LOGOS.keys())
    cols = st.columns(len(companies))
    for i, company in enumerate(companies):
        with cols[i]:
            logo_path = COMPANY_LOGOS[company]
            if os.path.exists(logo_path):
                if st.button("", key=f"btn_{i}"):
                    st.session_state["selected_company"] = company
                st.image(logo_path, width=96, caption=company)
            else:
                st.button(company, key=f"btn_{i}")
                st.write("(logo missing)")

    if st.session_state.get("selected_company"):
        comp = st.session_state["selected_company"]
        with st.modal(f"{comp} — Experience"):
            st.write(resume.get("experience", {}).get(comp, "No details found."))
            if st.button("Close"):
                st.session_state.pop("selected_company", None)

    st.markdown("---")
    st.subheader("Certifications")
    certs = list(CERT_LOGOS.keys())
    ccols = st.columns(len(certs))
    for i, cert in enumerate(certs):
        with ccols[i]:
            path = CERT_LOGOS[cert]
            if os.path.exists(path):
                st.image(path, width=120)
            st.caption(cert)

    st.markdown("---")
    st.subheader("Education")
    ed_c1, ed_c2 = st.columns([1, 3])
    with ed_c1:
        if os.path.exists(EDU_LOGO):
            st.image(EDU_LOGO, width=120)
    with ed_c2:
        st.write(resume.get("education", "").strip())

if __name__ == "__main__":
    main()
