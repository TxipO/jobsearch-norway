from gmail_client import get_service, search_messages

if __name__ == "__main__":
    service = get_service()
    profile = service.users().getProfile(userId="me").execute()
    print("Authenticated as:", profile["emailAddress"])

    messages = search_messages(service, "from:finn.no", max_results=5)
    print(f"Found {len(messages)} messages from finn.no")
