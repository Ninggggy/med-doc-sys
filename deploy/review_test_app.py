"""单进程本地测试启动器：真实产品蓝图、页面、业务服务和独立 SQLite。"""
import argparse
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


def create_app(root, frontend):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    os.environ['MYSQL_URL']='sqlite:///'+str(root/'test.sqlite')
    from flask import Flask,request,jsonify,send_from_directory
    from sqlalchemy import create_engine,event
    from sqlalchemy.orm import sessionmaker
    from agent.agent_backend.database.mysql.mysql_conn import Base
    from agent.agent_backend.database.mysql import db_model
    from agent.agent_backend.services.filing_change_review_service import FilingChangeReviewService
    from agent.agent_backend.application.filing_change_review_app_service import FilingChangeReviewAppService
    from agent.agent_backend.services.runtime_task_store import RuntimeTaskStore
    # 仅装载真实备案控制器，不执行聚合注册器中的无关LLM/RAG控制器。
    import importlib.util
    controller_path=Path(__file__).resolve().parents[1]/'agent_backend/controller/filing_change_review_controller.py'
    spec=importlib.util.spec_from_file_location('review_test_filing_controller',controller_path)
    controller=importlib.util.module_from_spec(spec);spec.loader.exec_module(controller)
    app=Flask(__name__,static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=100*1024*1024)
    engine=create_engine(os.environ['MYSQL_URL'],connect_args={'check_same_thread':False})
    @event.listens_for(engine,'connect')
    def foreign_keys(conn,_):conn.execute('PRAGMA foreign_keys=ON')
    names=['FilingChangeProject','FilingChangeApplicationForm','FilingChangeSubmissionFile','FilingChangeReferenceMaterial',
        'FilingChangeReviewRun','FilingChangeReviewResult','FilingChangeReviewReport','FilingChangeReviewRule','RuntimeTask']
    Base.metadata.create_all(engine,tables=[getattr(db_model,n).__table__ for n in names])
    class Connection:
        def __init__(self):self.engine=engine;self.factory=sessionmaker(bind=engine,expire_on_commit=False)
        def get_session(self):return self.factory()
    db=Connection();service=FilingChangeReviewService(db_conn=db,root_dir=root/'filing_change_review',enable_language_summary=False)
    controller._service=FilingChangeReviewAppService(service)
    controller._runtime_task_store=RuntimeTaskStore(connection=db)
    app.extensions['review_service']=service
    @app.before_request
    def same_origin():
        if not request.path.startswith('/api/'):return
        if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('Origin'):
            if urlsplit(request.headers['Origin']).netloc!=request.host:return jsonify(code=403,message='来源不匹配',data=None),403
    app.register_blueprint(controller.filing_change_review_bp,url_prefix='/api/filing-change-review')
    @app.route('/',defaults={'filename':'index.html'})
    @app.route('/<path:filename>')
    def static_file(filename):return send_from_directory(frontend,filename)
    return app


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--frontend',type=Path,required=True);p.add_argument('--port',type=int,default=5073);a=p.parse_args()
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    app=create_app(a.data,a.frontend.resolve())
    app.run(host='127.0.0.1',port=a.port,threaded=True,use_reloader=False)
