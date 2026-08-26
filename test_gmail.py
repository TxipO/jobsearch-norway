from gmail_client import fetch_plain_texts

if __name__ == "__main__":
    texts = fetch_plain_texts("from:finn.no")
    print(f"Found {len(texts)} messages from finn.no")
