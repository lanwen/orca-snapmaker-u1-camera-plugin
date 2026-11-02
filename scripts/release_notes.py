"""Tagged public notes shared by release PRs, GitHub releases, and Orca exports."""

import re

PUBLIC_PLACEHOLDER = "<!-- Write the user-facing changes here before merging this release PR. -->"
REVIEW_TEXT = "I reviewed the public notes against the generated changes below."


class ReleaseNotesError(ValueError):
    """Public release notes need editing or review."""


def tagged_section(body, kind):
    start = f"<!-- orca:{kind}-notes:start -->"
    end = f"<!-- orca:{kind}-notes:end -->"
    if start not in body and end not in body:
        return None
    if body.count(start) != 1 or body.count(end) != 1 or body.index(end) < body.index(start):
        raise ReleaseNotesError(f"{kind.capitalize()} notes must have exactly one correctly ordered start/end marker pair")
    return body.split(start, 1)[1].split(end, 1)[0].strip()


def public_section(body):
    return tagged_section(body, "public")


def generated_section(body):
    return tagged_section(body, "generated")


def notes_reviewed(body):
    checked = f"- [x] {REVIEW_TEXT}".casefold()
    return any(line.strip().casefold() == checked for line in body.splitlines())


def public_notes(body):
    section = public_section(body)
    if section is None:
        raise ReleaseNotesError("Missing tagged public release notes")
    notes = re.sub(r"<!--.*?-->", "", section, flags=re.DOTALL).strip()
    if not notes:
        raise ReleaseNotesError("Write public release notes before merging or publishing")
    if len(notes) > 4000:
        raise ReleaseNotesError("Public release notes must be at most 4000 characters")
    return notes


def format_notes(public, generated, *, public_first=True):
    public_block = ("## Public notes\n\n<!-- orca:public-notes:start -->\n" + public +
                    "\n<!-- orca:public-notes:end -->")
    generated_block = ("## Generated notes\n\n<!-- orca:generated-notes:start -->\n" + generated +
                       "\n<!-- orca:generated-notes:end -->")
    blocks = (public_block, generated_block) if public_first else (generated_block, public_block)
    return "\n\n".join(blocks) + "\n"
