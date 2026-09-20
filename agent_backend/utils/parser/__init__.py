from agent.agent_backend.utils.parser.parser_manager import ParserManager
from agent.agent_backend.utils.parser.doc_parser import parse_doc
from agent.agent_backend.utils.parser.pdf_parser import parse_pdf
from agent.agent_backend.utils.parser.docx_parser import parse_docx
from agent.agent_backend.utils.parser.material_parser import parse_material
from agent.agent_backend.utils.parser.markdown_parser import parse_markdown

ParserManager.register_parser("doc", parse_doc)
ParserManager.register_parser("pdf", parse_pdf)
ParserManager.register_parser("docx", parse_docx)
ParserManager.register_parser("txt", parse_material)
ParserManager.register_parser("md", parse_markdown)
