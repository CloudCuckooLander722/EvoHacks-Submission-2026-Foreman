import os
from typesafe_sdk import TypeSafeClient, Choice, Noul

def file_reader(file_path: str):
    """
    Reads the contents of a file, does questionaires and sends it along with another ai agent.

    Args:
        file_path (str): The path to the file to be read.

    Returns:
        str: The contents of the file.
    """
    with open(file_path, 'r') as file:
        content = file.read()

    warning_message = ""

    client = TypeSafeClient(api_key=os.getenv("TYPESAFE_API_KEY"))

    response = client.system_one(
        state=content[:4000],
        choices={
            "file_type": Choice(
                instructions="What file type is this?",
                options=["txt", "pdf", "docx", "csv", "json", "wav", "mp3", "mp4"]
            ),

            "is_valid": Noul(
                instructions="Is this extracted text readable and free from major corruption or gibberish?"
            ),
        }
    )

    is_valid_prob = response.nouls.get("is_valid")
    try:
        if is_valid_prob > 0.75:
            pass
        elif 0.25 <= is_valid_prob <= 0.75:
            warning_message += "The extracted audio / text quality may have some garbled or unreadable input."
        else:
            warning_message = "Too garbage for Jev to read. Please try again."
            raise ValueError(warning_message)
    except ValueError as e:
        print (f"Error: {e}")
        return None

    return {
        "content": content,
        "file_type": response.choices.get("file_type"),
        "validity": is_valid_prob,
        "warning_message": warning_message,
    }