---
name: ats-resume
description: Use when the user asks Neural to write, build, update, or tailor a resume/CV -- from scratch, from their existing career info, or targeted at a specific job posting (especially one found via search_jobs). Produces an ATS-safe .docx, never a visually "designed" resume with columns/tables/graphics.
---

# ATS-compliant resume writing

Most resumes get rejected by the Applicant Tracking System (ATS) before a
human ever sees them -- not on content, but on **format**: multi-column
layouts, tables, text boxes, headers/footers, icons, and creative fonts
all commonly fail to parse correctly, scrambling or losing information.
This skill produces resumes that are safe for ATS parsers first, and
still look clean and professional to a human reader second.

## 1. Gather the person's info (once, then reuse)

Before writing anything, check `memory/career.md` (in the project's memory/
folder, same convention as `memory/brand/<slug>.md` for content creation).
If it exists, read it and use it as the base -- don't re-ask for
everything each time, only for what's missing or what they want changed
for this specific version.

If it doesn't exist yet, ask for (can be one message, doesn't need to be
one question at a time):
- Full name, email, phone, city/region, LinkedIn URL (optional), portfolio/
  GitHub URL (optional).
- Work history: for each role -- title, company, location, start/end dates
  (or "Present"), and 3-6 bullet points of what they did (raw, ungroomed
  is fine, you'll tighten the language).
- Education: degree, school, location, dates, honors/GPA if they want it
  included.
- Skills (technical and/or soft, whatever's relevant to their field).
- Certifications, if any.

Once you have it, save it to `memory/career.md` in plain prose/lists (not
JSON) so it's easy for the user to read and correct later, and so future
resume requests skip straight to the next step. Update that file whenever
they give you new/corrected info.

## 2. If tailoring to a specific job

Ask for the job posting (a pasted description, a URL, or "the one you just
found via search_jobs"). Read it and note:
- The exact job title used in the posting (mirror it if the person's
  actual title is a close synonym -- ATS keyword-matches against the
  posting's own language).
- Hard skills / tools / technologies it names.
- Any required certifications or qualifications it calls out explicitly.

Weave those exact terms naturally into the summary, skills list, and
relevant bullet points -- **only for things the person actually has or
has done**. Never invent a skill or exaggerate a qualification just to
match keywords; that's resume fraud, not ATS optimization.

## 3. ATS formatting rules (why the build script is written this way)

- **Single column, no tables, no text boxes, no images/icons/logos.**
  Multi-column and table layouts are the single most common reason a
  real ATS misreads or scrambles a resume.
- **No header/footer placement for contact info.** Many parsers skip
  headers/footers entirely -- put name and contact info in the normal
  document body, at the top.
- **Standard section headings**, in this order: Summary, Skills,
  Experience, Education, (Certifications if any). Don't get creative
  with heading names ("My Journey", "What I Bring") -- ATS keys off
  conventional section names.
- **Reverse-chronological order** within Experience and Education (most
  recent first).
- **Standard, widely-embedded font** -- Calibri or Arial, 10.5-11pt body,
  never a decorative or script font.
- **Consistent date format** -- "Mon YYYY -- Mon YYYY" or "Mon YYYY --
  Present", the same style used everywhere in the document.
- **Bullets start with a strong action verb** and end with a
  quantifiable result where the person has one ("Cut deployment time
  40% by...", not "Responsible for deployments").
- **.docx, not PDF, by default.** Word format is parsed more reliably by
  more ATS systems than PDF, especially older/cheaper ATS installs. Only
  produce a PDF if the person specifically asks for one or a job
  application portal requires it -- and if so, convert the finished
  .docx rather than building a separate PDF from scratch, so the content
  stays identical.

## 4. Build it

`python-docx` is already in `backend/requirements.txt` (installed in the
project's venv). Use the ready-made builder in this skill folder rather
than writing docx-generation code from scratch each time:

```
python .claude/skills/ats-resume/scripts/build_ats_resume.py \
  --data /path/to/resume_data.json \
  --out output/FirstName_LastName_Resume.docx
```

(Paths are relative to wherever you run it from -- Neural's Bash cwd is
normally the project root, so `.claude/skills/...` and `output/...`
resolve correctly from there.)

`resume_data.json` shape (all fields except `name` and `experience` are
optional -- omit what doesn't apply rather than inventing filler):

```json
{
  "name": "Jordan Ahmed",
  "contact": {
    "email": "jordan@example.com",
    "phone": "+92 300 1234567",
    "location": "Lahore, Pakistan",
    "linkedin": "linkedin.com/in/jordanahmed",
    "portfolio": "jordanahmed.dev"
  },
  "summary": "2-3 sentence summary tailored to the target role, using its own language where true.",
  "skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "AWS"],
  "experience": [
    {
      "title": "Backend Engineer",
      "company": "Acme Corp",
      "location": "Remote",
      "start": "Jan 2023",
      "end": "Present",
      "bullets": [
        "Cut API p95 latency 40% by redesigning the caching layer",
        "Led migration of 12 services from REST to gRPC"
      ]
    }
  ],
  "education": [
    {
      "degree": "BSc Computer Science",
      "school": "University of the Punjab",
      "location": "Lahore",
      "start": "2019",
      "end": "2023",
      "details": "Dean's List, 2022"
    }
  ],
  "certifications": ["AWS Certified Solutions Architect – Associate"]
}
```

Write `resume_data.json` to a temp path (e.g. under `output/`) before
calling the script -- don't try to pass the whole structure as a shell
argument.

## 5. Hand it off

Save the finished file under `output/` (same convention as the project's
other generated documents), tell the user where it is, and mention in
plain language what you tailored it for if this was a job-specific
version. If they want a PDF too (for a portal that requires one), convert
that same .docx rather than rebuilding from the JSON, so the two versions
never drift apart -- e.g. via `soffice --headless --convert-to pdf` if
LibreOffice is available, or ask the user which converter they'd prefer
if it isn't.
