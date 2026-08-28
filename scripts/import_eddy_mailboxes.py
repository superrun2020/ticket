from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pymysql

from app import secret_box, now


EMAILS = [
    "oliver@woyonexus.com",
    "noc@junglelabs.space",
    "pujwkg@applenia.com",
    "rcqfcj@kennymoney.com",
    "qvqieg@justappadv.com",
    "vdzpwq@singsarepro.com",
    "uiatib@offereven.com",
    "bqemkk@mobliyuh.com",
    "typuec@adgreet.com",
    "asjans@singaredemocracy.com",
    "xiuyjz@mobiprofound.com",
    "hmcmmf@flagssap.com",
    "auitbw@fiveoneads.com",
    "sgyius@flyappsdate.com",
    "pwkkpc@flysdata.com",
    "gukxdx@flysprovide.com",
    "hrmbnq@ghghmn.com",
    "kbpgnh@ghghmrt.com",
    "uxstju@ghghmsd.com",
    "isimqg@ghghmvc.com",
    "yqngbg@ghghnm.com",
    "mzujnv@ghghvb.com",
    "dysdpx@ghghvc.com",
    "bayjub@ke8oo.com",
    "ukehap@npowhatanstyle.com",
    "exiexr@pdealyersap.com",
    "hjvzfc@perssap.com",
    "ckuayu@fiveoneads.com",
    "empwda@flagssap.com",
    "ytgxam@flyappsdate.com",
    "qbjsym@posterssap.com",
    "vxknjg@binklv.com",
    "dwtine@poutb.com",
    "cruzyp@sapdate.com",
    "swbyxc@ghghmvc.com",
    "cfgumm@app99ss.com",
    "pxdqbd@ghghvb.com",
    "yvirpf@ke8oo.com",
    "avmjhs@ghghbn.com",
    "tphdrt@ghghcv.com",
    "jpjehu@ghghjj.com",
    "xrmwdq@walkks.com",
    "esniri@fiveoneads.com",
    "xehsrq@flagssap.com",
    "uaqgph@flyappsdate.com",
    "ttvinx@posterssap.com",
    "lily@51playable.com",
    "adbink_finance@woyonexus.com",
    "appcone_finance@woyonexus.com",
    "aba_finance@woyonexus.com",
    "sora_finance@woyonexus.com",
    "insp_finance@woyonexus.com",
    "selectad_finance@woyonexus.com",
    "aigcmobile_finance@woyonexus.com",
    "willam@51playable.com",
    "dugwkg@applenia.com",
    "beidaikun@tuander.com",
    "service@apptilaus.com",
    "marver@metebok.com",
    "finance@aismartmobi.com",
    "adsvigor_finance@woyonexus.com",
    "tech@woyonexus.com",
    "noc@soraads.net",
    "noc@adbink.com",
    "noc@aigcmobile.com",
    "noc@smaigc.com",
    "sappoc@51playable.com",
    "teams@soraads.net",
    "noc@adsprotractor.com",
    "noc@aismartmobi.com",
    "noc@inspiremobi.com",
    "noc@selectad.cc",
    "noc@appcone.net",
    "kestrel@200mbti.com",
    "jet@personalitiess.com",
    "moss@the16personalities.com",
    "pine@mbtibydeepseek.com",
    "orchid@viralsphered.com",
    "umber@designhubse.com",
    "nova@famefluenced.com",
    "linden@personalitytoday.com",
    "reed@thepocketdrama.com",
    "slate@my16types.com",
    "support@rufreevpn.net",
    "noc@freevpn.network",
    "network@freevpn.network",
    "support@freevpn.network",
    "billing@freevpn.network",
    "abuse@freevpn.network",
    "legal@freevpn.network",
    "support@xxvpn.co",
    "astraai@freeaipro.com",
    "abuse@woyonexus.com",
    "rosc@woyonexus.com",
    "support@rocketspacevpn.com",
    "support1@rocketspacevpn.com",
    "haijun.lyu@woyonexus.com",
    "support@vpn.geekforest.ai",
    "lir@junglelabs.space",
    "notice@junglelabs.uk",
    "pm@oa.geekforest.ai",
    "support@familychronica.com",
    "hello@familychronica.com",
    "accounts@familychronica.com",
    "no-reply@familychronica.com",
]

PID_EMAILS = set(EMAILS[:47])
ASN_LOCALS = {"noc", "network", "rosc", "tech"}


def mailbox_tag(address: str) -> str:
    local = address.split("@", 1)[0]
    if address in PID_EMAILS:
        return "PID邮箱"
    if local in ASN_LOCALS:
        return "ASN邮箱"
    if "finance" in local or local in {"teams", "service"}:
        return "网盟邮箱"
    return "产品邮箱"


def main() -> None:
    required = ("EDDY_SOURCE_DB_HOST", "EDDY_SOURCE_DB_USER", "EDDY_SOURCE_DB_PASSWORD", "TICKET_SESSION_SECRET")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError("Missing environment: " + ", ".join(missing))
    mysql = pymysql.connect(
        host=os.environ["EDDY_SOURCE_DB_HOST"],
        port=int(os.getenv("EDDY_SOURCE_DB_PORT", "3306")),
        user=os.environ["EDDY_SOURCE_DB_USER"],
        password=os.environ["EDDY_SOURCE_DB_PASSWORD"],
        database=os.getenv("EDDY_SOURCE_DB_NAME", "dev_perform"),
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )
    encrypted = secret_box()
    try:
        with mysql.cursor() as cursor:
            for address in EMAILS:
                local = address.split("@", 1)[0]
                password = f"pass@{local}1"
                tag = mailbox_tag(address)
                cursor.execute(
                    """INSERT INTO eddy_ticket_mailboxes
                    (project_code,display_name,mailbox_email,imap_host,imap_port,imap_encryption,imap_username,
                     smtp_host,smtp_port,smtp_encryption,smtp_username,password_ciphertext,mail_folder,enabled,mailbox_tag)
                    VALUES (%s,%s,%s,'mail.willech.com',993,'ssl',%s,'mail.willech.com',465,'ssl',%s,%s,'INBOX',1,%s)
                    ON DUPLICATE KEY UPDATE display_name=VALUES(display_name),imap_host=VALUES(imap_host),
                    imap_username=VALUES(imap_username),smtp_host=VALUES(smtp_host),smtp_username=VALUES(smtp_username),
                    password_ciphertext=VALUES(password_ciphertext),enabled=1,mailbox_tag=VALUES(mailbox_tag)""",
                    (local, local, address, address, address, encrypted.encrypt(password.encode()).decode(), tag),
                )
            cursor.execute(
                """INSERT INTO eddy_ticket_mailbox_audit
                (mailbox_id,action,actor,changed_fields,source_ip)
                VALUES (NULL,'bulk_import','ticket-system',JSON_OBJECT('count',%s,'mail_host','mail.willech.com'),NULL)""",
                (len(EMAILS),),
            )
        mysql.commit()
        with mysql.cursor() as cursor:
            cursor.execute("SELECT id,project_code,display_name,mailbox_email,mailbox_tag,enabled FROM eddy_ticket_mailboxes WHERE enabled=1")
            rows = cursor.fetchall()
    finally:
        mysql.close()

    db_path = Path(__file__).resolve().parents[1] / "data" / "tickets.db"
    sqlite = sqlite3.connect(db_path)
    try:
        timestamp = now()
        for row in rows:
            sqlite.execute(
                """INSERT INTO mailboxes(id,name,email,color,created_at,enabled,workspace_id,mailbox_tag)
                VALUES(?,?,?,?,?,1,'eddy-personal',?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,email=excluded.email,color=excluded.color,
                enabled=1,workspace_id='eddy-personal',mailbox_tag=excluded.mailbox_tag""",
                (f"eddy-{row['id']}", row["display_name"] or row["project_code"], row["mailbox_email"], "#1b9aaa", timestamp, row["mailbox_tag"]),
            )
            sqlite.execute(
                "INSERT OR IGNORE INTO mailbox_sync(mailbox_id,last_uid,updated_at) VALUES(?,0,?)",
                (f"eddy-{row['id']}", timestamp),
            )
        sqlite.commit()
    finally:
        sqlite.close()
    print(f"Imported {len(rows)} Eddy mailboxes; requested batch {len(EMAILS)}.")


if __name__ == "__main__":
    main()

