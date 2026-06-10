path = "apps/api/app/services/sharepoint_ingestion_service.py"
content = open(path, encoding="utf-8").read()

old = (
    '    to_emails: Optional[str] = payload.get("toEmails") or None\n'
    '    cc_emails: Optional[str] = payload.get("ccEmails") or None\n'
    '    bcc_emails: Optional[str] = payload.get("bccEmails") or None'
)

new = (
    '    # SP column names are PascalCase: ToEmails, CCEmails, BccEmails\n'
    '    # Try PascalCase first (SharePoint native), fall back to camelCase\n'
    '    to_emails: Optional[str]  = payload.get("ToEmails")  or payload.get("toEmails")  or None\n'
    '    cc_emails: Optional[str]  = payload.get("CCEmails")  or payload.get("ccEmails")  or None\n'
    '    bcc_emails: Optional[str] = payload.get("BccEmails") or payload.get("bccEmails") or None'
)

count = content.count(old)
print(f"Found {count} occurrence(s) — replacing all")
updated = content.replace(old, new)
open(path, "w", encoding="utf-8").write(updated)
print("Done — verifying:")
import ast
ast.parse(updated)
print("SYNTAX OK")
