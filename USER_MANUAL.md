# CRM Portal — User Manual

This guide is for **end users**: **Administrators** and **Counsellors**. It explains how to use each feature in the web application.  
(Technical setup for developers is in [DOCUMENTATION.md](./DOCUMENTATION.md).)

---

## 1. Signing in and signing out

### 1.1 Login
1. Open your organisation’s CRM URL (provided by your administrator).
2. Enter your **email** and **password**.
3. Click **Sign me in**.

You are sent to the correct home screen based on your role:
- **Admin** → Admin dashboard  
- **Counsellor** → Counsellor dashboard  

### 1.2 Logout
- Click your **Logout** control in the top navigation (usually top-right).

### 1.3 Forgot password
- Use **Forgot Password?** on the login page if your organisation has email configured. Follow the link in the email to set a new password.

---

## 2. Understanding roles

| Role | What they do |
|------|----------------|
| **Administrator (Admin)** | Configure the CRM, see **all** leads, manage counsellors and (if allowed) other admins, import data, assign leads, run reports. |
| **Counsellor** | Work **only on leads assigned to them**: update status, log activities, schedule visits, create businesses, request transfers. |

Some menu items are **hidden** if your admin account has restricted permissions (see section 3.6).

---

## 3. Administrator guide

### 3.1 Dashboard
**Menu:** Dashboard  

Shows organisation-wide summaries, for example:
- Total counsellors, leads, and converted (won) leads  
- Lead counts by status  
- Monthly lead and business trends (charts)  
- Lead source breakdown  
- Counsellor performance summary  
- Recent activities  

Use the **quick action** buttons (if shown) to jump to **Import Leads** or **Assign Leads**.

---

### 3.2 Manage Leads
**Menu:** Manage Leads  

**What you can do**
- **Browse** all leads (paginated; e.g. 50 per page).
- **Filter** by search text, status, priority, counsellor, and source — then click **Filter**.
- **Clear filters** when you want to see everything again.

**Toolbar buttons**
| Button | Purpose |
|--------|---------|
| **Add Lead** | Create one lead manually. |
| **Import Leads** | Upload a spreadsheet (`.csv` or `.xlsx`) to create many leads at once. |
| **Assign Leads** | Open the bulk assignment screen for unassigned (or reassigned) leads. |
| **Delete all leads** *(if you have delete permission)* | **Permanently removes every lead in the system** (not only the current page). Opens a confirmation window — you must type exactly: `DELETE ALL LEADS`. Use only when you intend to wipe all lead data. |

**Per-lead row actions**
- **View** — full lead details (admin view).  
- **Edit** — change lead fields.  
- **Transfer** — move the lead to another counsellor.  
- **Delete** — remove this lead *(only if your account has delete permission)*.  

**Bulk selection**
- Use the checkboxes on the left to select leads on the **current page**.  
- Click **Delete Selected** to delete only those IDs *(requires delete permission)*.  
- To delete leads on other pages, change the page or use filters; **Delete all leads** removes the entire database of leads regardless of page.

**Import file columns (typical)**  
Your administrator may give you a template. Commonly expected columns include:
- **Required:** `first_name`, `last_name`, `email`, `phone`, `course_interested`  
- **Optional:** `alternate_phone`, `School Name`, `graduation_status` (YES/NO), `graduation_course`, `graduation_year`, `graduation_college`, `industry`  

During import you choose a **lead source** and may assign a counsellor or use **auto-assign** options.

---

### 3.3 Manage Counsellors
**Menu:** Manage Counsellors  

- **Add** counsellors (creates their login).  
- **Edit** profile details (e.g. employee ID, department).  
- **Deactivate** or remove counsellors according to your process.  

Counsellors only see leads **assigned** to them.

---

### 3.4 Manage Admins
**Menu:** Manage Admins *(visible only to **superadmin** accounts)*  

- Add or edit **administrator** users.  
- Set **permissions** (e.g. who may delete leads, view performance, manage reference data).  

---

### 3.5 Lead Sources
**Menu:** Lead Sources *(requires **Settings** permission)*  

Maintain the list of sources (e.g. Website, Walk-in, Campaign). These appear when adding or importing leads.

---

### 3.6 Lead Statuses
**Menu:** Lead Statuses *(requires **Settings** permission)*  

Configure which **status** values exist for leads (e.g. New, Contacted, Qualified).  
The pipeline should match how your institute tracks enquiries.

---

### 3.7 Activity Types
**Menu:** Activity Types *(requires **Settings** permission)*  

Define types of activities counsellors log (e.g. Call, Email, Visit). Icons and labels can be customised.

---

### 3.7a WhatsApp & Meta
**Menu:** WhatsApp & Meta *(requires **Settings** permission)*  

Connect **WhatsApp** (Cloud API), **Instagram** DMs, and **Facebook Page** (Messenger) so inbound chats create or update **leads** (and optionally notify admins).

1. Open **WhatsApp & Meta** and set your **public site URL** (HTTPS), **verify token**, **app secret**, and (if you will send replies from the app) **access token** and **WhatsApp phone number ID** — matching what you configure in [Meta for Developers](https://developers.facebook.com/).
2. Copy the **webhook callback URL** into your Meta app and subscribe: **`messages`** for WhatsApp; **Instagram** and/or **Page** products for their messaging webhooks.
3. Turn on **Accept WhatsApp inbound**, **Accept Instagram DMs**, and/or **Accept Facebook Page / Messenger** to match what you enabled in Meta.

Technical details and environment overrides are in [DOCUMENTATION.md](./DOCUMENTATION.md) §14.

**Social inbox:** Under the same permission group, **Social inbox** lists every WhatsApp / Instagram / Facebook conversation. Select a thread to read messages and send replies (subject to Meta’s messaging rules and your tokens).

---

### 3.8 Next Actions (reference list)
**URL:** `/next-actions/manage/` (bookmark if not linked in your sidebar)  

Manage the list of **next action** options (e.g. Callback, Send brochure) used when logging activities.  
System-defined entries may be protected from deletion.

---

### 3.9 Daily Targets
**Menu:** Daily Targets  

- Create or manage **daily targets** for counsellors (e.g. calls or visits expected per day).  
- Assign targets to counsellors for specific dates.  

Counsellors see their own target progress under **Today’s Targets**.

---

### 3.10 Counsellor Activity
**Menu:** Counsellor Activity  

Report-style view of counsellor pipeline and activity-related metrics (separate from the main dashboard charts). Use it to monitor workload and consistency.

---

### 3.11 Performance
**Menu:** Performance *(requires **Performance** permission)*  

Analytics focused on counsellor **performance** (conversion, workload, etc.). Exact charts depend on your build.

---

### 3.12 Counsellor Work
**Menu:** Counsellor Work *(requires **Counsellor work** permission)*  

Operational view of how counsellors are working leads (visits, activities in a date range, etc.).

---

### 3.13 Assign Leads (bulk)
**Path:** From **Manage Leads** → **Assign Leads**, or dashboard shortcut.  

- Choose an **assignment method** (e.g. round-robin, workload-balanced).  
- Run the assignment so **unassigned** leads get distributed to active counsellors.  

Use this after imports or when rebalancing the team.

---

### 3.14 Transfer a single lead
From **Manage Leads**, use **Transfer** on a row to move that lead to another counsellor (with any notes your process requires).

---

### 3.15 Businesses (organisation view)
**URL:** `/businesses/manage/`  

List and oversee **business** records created from qualified leads (value, status, counsellor). Use this for revenue / pipeline oversight if your organisation enables it.

---

### 3.16 Notifications (admin)
**Top bar:** notification icon  

View messages sent to admins; mark as read or clear as needed.  
Admins may also **send notifications** to counsellors where the feature is exposed (e.g. **Send notification** flows in the admin area).

---

### 3.17 Profile
Use **profile** / account links in the header (if present) to view or update your admin profile details.

---

## 4. Counsellor guide

### 4.1 Dashboard
**Menu:** Dashboard  

Your personal overview:
- Counts of your leads by status  
- Today’s scheduled activities and visits  
- Upcoming follow-ups for today  
- Recent activity history  
- Progress toward **today’s target** (if configured)  
- Optional **calendar** preview with a link to the full calendar  

Pop-up reminders may appear for due activities or visits (browser must allow the page to work normally).

---

### 4.2 My Leads
**Menu:** My Leads  

- Lists **only leads assigned to you**.  
- Filter by **status** if available.  
- Open a lead to see **detail**, **timeline**, and **calendar** for that lead.  

From a lead you can:
- **Edit** allowed fields  
- **Update status**  
- **Add activity** (call, visit, note, etc.)  
- **Schedule** or **reschedule** next visit / follow-up  
- **Mark lead as lost** (if your workflow uses it)  
- **Request transfer** if the lead should belong to someone else  
- **Create business** when a lead becomes a paying opportunity  
- **Evaluate conversion** (if AI / scoring is enabled)  
- Manage **alternate phone** numbers where supported  
- Some **phone** fields may require a **reveal** action for privacy logging  

**Activities and “next action”**  
When logging an activity, you can indicate whether there is a **next action** and choose from the configured **next action** list. That drives **Pending Tasks**.

---

### 4.3 My Businesses
**Menu:** My Businesses  

Opportunities you created from leads: value, status (e.g. Pending, Active), dates. Open a record to update status or add notes as your process allows.

---

### 4.4 My Activities
**Menu:** My Activities  

A consolidated list of activities you logged across leads — useful for reviewing recent work.

---

### 4.5 Today’s Targets
**Menu:** Today’s Targets  

- Shows your **daily target** for today (if assigned by an admin).  
- Lists **today’s visits** and progress counts that count toward the target.  

Complete activities and visits as you work so progress stays accurate.

---

### 4.6 Pending Tasks
**Menu:** Pending Tasks *(badge may show a count)*  

- Activities waiting on a **next action**  
- **Upcoming visits** you still need to complete or reschedule  

Use **Log the next action** (or similar) to jump to the activity form for that lead.

---

### 4.7 Calendar
**From dashboard:** link to **My Calendar**, or open **Counsellor Calendar** from your dashboard calendar section.  

- Month/week view of **scheduled activities** and **visits**.  
- Helps plan your day alongside **Today’s Targets**.

---

### 4.8 Lead detail — list vs calendar
On a lead’s page you can often switch between a **list** of activities and a **calendar** view of the same lead’s schedule.

---

### 4.9 Notifications
**Top bar:** notification icon  

Read counsellor notifications from admins or the system. Dismiss or delete per your UI.

---

### 4.10 My Profile
**Menu:** My Profile  

Update your profile information where your organisation allows (name, contact, password changes if implemented).

---

## 5. Lead status workflow (typical)

Exact names depend on your **Lead Statuses** configuration. A common flow:

1. **NEW** — Just captured.  
2. **CONTACTED** — First touch done.  
3. **QUALIFIED** — Fits criteria; may move toward admission / business.  
4. **PROPOSAL_SENT** / **NEGOTIATION** — If your institute uses these stages.  
5. **CLOSED_WON** — Successfully converted (often linked to **Business**).  
6. **CLOSED_LOST** — Did not convert.  
7. **TRANSFERRED** — Handed to another counsellor (may be set automatically on transfer).  

Counsellors update status from the **lead detail** screen; admins can edit from **Manage Leads**.

---

## 6. Tips and good practice

- **After bulk import**, use **Assign Leads** so counsellors receive work.  
- Use **filters** on Manage Leads instead of scrolling thousands of rows.  
- **Delete all leads** is irreversible — use only for test environments or after explicit approval.  
- Keep **lead source** and **status** lists aligned with how management reports metrics.  
- Complete **activities** and **visits** the same day when possible so dashboards and targets stay truthful.  

---

## 7. Getting help

- **Login or access issues** — contact your **CRM administrator** or IT.  
- **Wrong menu items** — your admin account may have **restricted permissions**; ask a superadmin to adjust them.  
- **Bugs or slowness** — report URL, time, and what you clicked; your technical team can use [DOCUMENTATION.md](./DOCUMENTATION.md) for deployment and logs.  

---

*This manual matches the CRM Portal application structure. If your organisation customises labels or hides features, follow local training materials first.*
