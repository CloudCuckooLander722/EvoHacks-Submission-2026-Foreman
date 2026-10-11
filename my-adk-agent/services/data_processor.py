import os
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typesafe_sdk import Choice, Noul, TypeSafeClient, TypeSafeError

MAX_STATE_CHARS = 4000
FileType = Literal["txt", "pdf", "docx", "csv", "json", "wav", "mp3", "mp4"]
FILE_TYPES = list(get_args(FileType))

class OutputSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, description="The full text extracted from the input file.")
    file_type: FileType | None = Field(description="The detected type of the input file, or None if it couldn't be determined.")
    validity: float = Field(ge=0.0, le=1.0, description="Probability (0 to 1) that the extracted text is readable and not corrupted.")
    warning_message: str = Field(default="", description="Any quality or truncation warnings about the extracted text; empty if none.")



def file_reader(file_path: str):
    """
    Reads the contents of a file, does questionaires and sends it along with another ai agent.

    Args:
        file_path (str): The path to the file to be read.

    Returns:
        dict | None: An OutputSchema dict: the contents of the file plus its detected type, validity score and any
        warning, or None if the file could not be read or failed the validity check.
    """
    # Edge case: no path given
    if not file_path or not isinstance(file_path, str):
        print("Error: No file path was given.")
        return None

    # Edge case: path doesn't exist or isn't a regular file
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        return None
    if not os.path.isfile(file_path):
        print(f"Error: Not a file: {file_path}")
        return None

    # Edge case: unreadable or binary (non-text) file
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
    except PermissionError:
        print(f"Error: No permission to read: {file_path}")
        return None
    except UnicodeDecodeError:
        print(f"Error: {file_path} is not a UTF-8 text file. Extract its text first.")
        return None

    # Edge case: empty or whitespace-only file, nothing worth sending to the API
    if not content.strip():
        print(f"Error: {file_path} is empty.")
        return None

    warning_message = ""

    # Edge case: only the first MAX_STATE_CHARS characters get checked
    if len(content) > MAX_STATE_CHARS:
        warning_message += f"Only the first {MAX_STATE_CHARS} characters were checked for quality. "

    # Edge case: missing API key
    api_key = os.getenv("TYPESAFE_API_KEY")
    if not api_key:
        print("Error: TYPESAFE_API_KEY is not set.")
        return None

    # Edge case: API/network failure
    try:
        client = TypeSafeClient(api_key=api_key)
        response = client.system_one(
            state=content[:MAX_STATE_CHARS],
            questions={
                "file_type": Choice(
                    instructions="What file type is this?",
                    criteria={file_type: None for file_type in FILE_TYPES}
                ),

                "is_valid": Noul(
                    instructions="Is this extracted text readable and free from major corruption or gibberish?"
                ),
            }
        )
    except TypeSafeError as e:
        print(f"Error: TypeSafe request failed: {e}")
        return None

    # Edge case: answer missing from the response
    is_valid_answer = response.nouls.get("is_valid")
    if is_valid_answer is None:
        print("Error: TypeSafe response had no validity answer.")
        return None
    is_valid_prob = is_valid_answer.noul

    file_type_answer = response.choices.get("file_type")
    file_type = file_type_answer.choice if file_type_answer else None

    if is_valid_prob > 0.75:
        pass
    elif 0.25 <= is_valid_prob <= 0.75:
        warning_message += "The extracted audio / text quality may have some garbled or unreadable input."
    else:
        print("Error: Too garbage for Jev to read. Please try again.")
        return None

    # Edge case: API returned values outside the schema (e.g. an unknown file type)
    try:
        output = OutputSchema(
            content=content,
            file_type=file_type,
            validity=is_valid_prob,
            warning_message=warning_message.strip(),
        )
    except ValidationError as e:
        print(f"Error: Output failed validation: {e}")
        return None

    return output.model_dump()

