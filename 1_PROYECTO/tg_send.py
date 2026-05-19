"""Quick TG notifier."""
import sys, requests
TOKEN = "8393198023:AAFbGB0pSzCyTujXb7orA0C-mSFUcQycOsg"
CHAT_ID = 326155958
def send(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          json={"chat_id":CHAT_ID,"text":msg,"parse_mode":"HTML"},
                          timeout=10)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"TG fail: {e}")
        return False
if __name__ == "__main__":
    msg = " ".join(sys.argv[1:])
    print(send(msg))
