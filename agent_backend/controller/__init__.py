from flask import Flask

from agent.agent_backend.controller.file_controller import file_bp
from agent.agent_backend.controller.knowledge_controller import knowledge_bp
from agent.agent_backend.controller.pharmacopeia_controller import pharmacopeia_bp
from agent.agent_backend.controller.pre_review_controller import pre_review_bp
from agent.agent_backend.controller.qa_controller import qa_bp
from agent.agent_backend.controller.rl_controller import rl_bp
from agent.agent_backend.controller.filing_change_review_controller import filing_change_review_bp


def register_controllers(app: Flask) -> None:
    app.register_blueprint(file_bp)
    app.register_blueprint(knowledge_bp)
    app.register_blueprint(pharmacopeia_bp)
    app.register_blueprint(pre_review_bp)
    app.register_blueprint(qa_bp)
    app.register_blueprint(rl_bp)
    app.register_blueprint(filing_change_review_bp)
