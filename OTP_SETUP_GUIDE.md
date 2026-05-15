# 📧 OTP Email Setup Guide - SecurePay AI

## Problem Solved ✅
The OTP email sending was failing because **SMTP credentials weren't configured**. This guide will help you set up Gmail for OTP delivery.

---

## Step-by-Step Setup

### **Step 1: Prepare Your Gmail Account**

1. Go to: https://myaccount.google.com/security
2. Scroll down and find **"2-Step Verification"**
   - If not enabled, click "Enable 2-Step Verification" and follow the prompts
   - This is **REQUIRED** to create App Passwords

### **Step 2: Create Gmail App Password**

1. After 2-Step Verification is enabled, go back to Security Settings
2. Look for **"App passwords"** in the Security section (appears only after 2-Step is enabled)
3. Select:
   - **App:** Mail
   - **Device:** Windows Computer
4. Click **"Generate"**
5. Google will show a 16-character password like: `xxxx xxxx xxxx xxxx`
6. **Copy this password** (including spaces or without - both work)

### **Step 3: Update .env File**

Open the `.env` file in the project root and update it:

```env
# Gmail SMTP Configuration
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=your-email@gmail.com
SENDER_PASSWORD=xxxx-xxxx-xxxx-xxxx
ADMIN_EMAIL=admin@securepay.local
```

Replace:
- `your-email@gmail.com` → Your actual Gmail address
- `xxxx-xxxx-xxxx-xxxx` → The 16-character App Password from Step 2

### **Step 4: Restart Flask Server**

The Flask server is already checking for the `.env` file. You need to:
1. Stop the current Flask server (CTRL+C)
2. Restart it with: `py -3 -m flask run --reload`

The server will now load your SMTP credentials from the `.env` file.

---

## Testing the OTP System

Once configured:

1. Go to: http://127.0.0.1:5000/register
2. Enter your email address
3. Click **"Send OTP"**
4. Check your email inbox for the OTP code
5. Enter the 6-digit code and verify
6. Complete the registration

---

## Common Issues & Fixes

### ❌ "OTP not arriving in email"
**Solution:** Check that:
- ✅ Gmail Account has **2-Step Verification enabled**
- ✅ `.env` file has correct `SENDER_PASSWORD` (16 chars, no typos)
- ✅ Flask server restarted AFTER updating `.env`
- ✅ Check spam/junk folder in Gmail

### ❌ "Failed to send OTP" error message
**Possible causes:**
1. **SENDER_PASSWORD not set** → Update `.env` file
2. **Gmail App Password incorrect** → Recreate App Password and update `.env`
3. **2-Step Verification not enabled** → Enable it in Security Settings
4. **Flask server not restarted** → Stop and restart Flask

### ❌ "ModuleNotFoundError: No module named 'dotenv'"
**Solution:** Install python-dotenv:
```powershell
py -3 -m pip install python-dotenv
```

---

## How It Works

1. **User registration form** → User enters email and requests OTP
2. **OTP generation** → System generates random 6-digit code
3. **SMTP connection** → Connects to Gmail SMTP using credentials in `.env`
4. **Email sending** → Sends OTP to user's email via Gmail
5. **OTP verification** → User enters code to verify email
6. **Account creation** → Only after verification, user can set username/password

---

## Security Notes

⚠️ **Important:**
- `.env` file contains sensitive credentials
- **NEVER commit `.env` to GitHub** (add to `.gitignore`)
- Use **App Password**, NOT your regular Gmail password
- App Password is specific to this application only
- You can delete/regenerate App Passwords anytime

---

## Environment Variables Reference

| Variable | Value | Example |
|----------|-------|---------|
| `SMTP_SERVER` | Gmail SMTP server | `smtp.gmail.com` |
| `SMTP_PORT` | TLS port for Gmail | `587` |
| `SENDER_EMAIL` | Your Gmail address | `myemail@gmail.com` |
| `SENDER_PASSWORD` | Gmail App Password (16 chars) | `abcd-efgh-ijkl-mnop` |
| `ADMIN_EMAIL` | Admin notification email | `admin@securepay.local` |

---

## After Setup

Once configured, the OTP system will:
- ✅ Send 6-digit OTP to user's email
- ✅ OTP expires in 5 minutes
- ✅ Users get 3 attempts to verify
- ✅ After verification, can set username/password
- ✅ Send welcome email after complete registration

---

## Need Troubleshooting?

1. **Check Flask terminal logs** for error messages when sending OTP
2. **Check email spam folder** for OTP emails
3. **Verify `SENDER_PASSWORD`** is exactly 16 characters from Gmail App Password
4. **Make sure `SENDER_EMAIL`** matches your Gmail address
5. **Restart Flask server** after any `.env` changes

