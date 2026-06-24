import re
from pathlib import Path

extractors_dir = Path('app/ai/extractors')

for path in extractors_dir.glob('section*_extractor.py'):
    content = path.read_text(encoding='utf-8')
    if 'field_catalog' in content:
        print('skip', path.name)
        continue

    content = re.sub(
        r'async def (extract_section\d+_from_document)\(\s*\n\s*document_url: str,\s*\n\s*subsection: str \| None = None,\s*\n\s*mime_type: str = "application/pdf",\s*\n\):',
        r'async def \1(\n    document_url: str,\n    subsection: str | None = None,\n    mime_type: str = "application/pdf",\n    field_catalog: list[dict] | None = None,\n):',
        content,
        count=1,
    )

    content = content.replace(
        'return await extract_structured_from_document(\n        document_url=document_url,\n        mime_type=mime_type,\n        prompt=prompt,\n        response_schema=',
        'return await extract_structured_from_document(\n        document_url=document_url,\n        mime_type=mime_type,\n        prompt=prompt,\n        field_catalog=field_catalog,\n        response_schema=',
    )

    path.write_text(content, encoding='utf-8')
    print('patched', path.name)
