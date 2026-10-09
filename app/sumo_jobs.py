"""Threaded, bounded local SUMO experiments. Each run owns a TraCI connection."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import subprocess
import sys
from threading import RLock
from uuid import uuid4

from app.models import SimulationRequest
from app.research import ResearchRequest, run_study
from app.network import ROOT
from app.sumo_runner import run_sumo
from app.areas import network_path

_LOCK=RLock()
_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix="rome-sumo")
_JOBS={}


def _update(job_id,**values):
    with _LOCK:
        _JOBS[job_id].update(values)


def info(job_id):
    with _LOCK:
        job=_JOBS.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return {k:v for k,v in job.items() if k!="result"}


def result(job_id):
    with _LOCK:
        job=_JOBS.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["status"]!="complete":
            raise RuntimeError("Result not yet available: "+job["status"])
        return job["result"]


def latest_setup(area="salaria"):
    """Most recent import attempt, if any, to render readiness in the dashboard."""
    with _LOCK:
        for job in reversed(list(_JOBS.values())):
            if job["kind"] == "setup" and job.get("area","salaria")==area:
                return {k: v for k, v in job.items() if k != "result"}
    return None


def submit(kind,request=None):
    if kind not in {"setup","run","compare","research"}:
        raise ValueError("Unsupported job type")
    if kind=="setup" and request not in {None,"salaria","nord_est"}:
        raise ValueError("Unknown study area")
    if kind=="research" and not isinstance(request,ResearchRequest):
        raise ValueError("ResearchRequest required")
    if kind not in {"setup","research"} and not isinstance(request,SimulationRequest):
        raise ValueError("SimulationRequest required")
    with _LOCK:
        if kind == "setup":
            for job in _JOBS.values():
                if (job["kind"] == "setup"
                        and job.get("area","salaria")==(request or "salaria")
                        and job["status"] in {"queued", "running"}):
                    return {k: v for k, v in job.items() if k != "result"}
        running=sum(j["status"] in {"queued","running"} for j in _JOBS.values())
        if running>=2:
            raise RuntimeError("SUMO is busy; at most two queued/running jobs")
        if len(_JOBS)>=8:
            for key in list(_JOBS):
                if _JOBS[key]["status"] not in {"queued","running"}:
                    del _JOBS[key]
                    break
        jid=str(uuid4())
        _JOBS[jid]={"id":jid,"kind":kind,
                    "area": (request or "salaria") if kind=="setup" else
                            request.scenario.area if kind=="research" else request.area,
                    "status":"queued",
                    "progress":0.,"message":"Waiting for SUMO worker",
                    "submitted_at":datetime.now(timezone.utc).isoformat()}
    _POOL.submit(_execute,jid,kind,request)
    return info(jid)


def _execute(jid,kind,request):
    _update(jid,status="running",message="SUMO started",progress=.01)
    def progress(value,message):
        _update(jid,progress=round(value,3),message=message)
    try:
        if kind=="setup":
            progress(.1,"Downloading OSM & building SUMO net (may take several minutes)")
            process=subprocess.run(
                [sys.executable,str(ROOT/"scripts"/"bootstrap_sumo.py"),
                 "--area", request or "salaria"],
                cwd=str(ROOT),capture_output=True,text=True,timeout=1800,
                check=False,
            )
            if process.returncode:
                detail = (process.stdout + "\n" + process.stderr).strip()
                raise RuntimeError(detail[-2000:] or "OSM import / netconvert failed without diagnostics")
            data={"message":process.stdout[-2000:],
                  "network_file":str(network_path(request or "salaria"))}
            # Invalidate any cached SUMO geo layout when rebuilding.
            from app.sumo_runner import load_network
            load_network.cache_clear()
            # A syntactically valid net is not necessarily a routable Salaria net.
            from app.sumo_runner import status
            if not status(request or "salaria")["available"]:
                raise RuntimeError("SUMO road network was not created")
            progress(.98, "SUMO network prepared")
        elif kind=="run":
            data=run_sumo(request,progress=progress)
        elif kind=="research":
            data=run_study(request,progress=progress)
        else:
            baseline=request.model_copy(update={"mode":"manual","overrides":{}})
            def first(p,msg):progress(p*.48,"Baseline: "+msg)
            def second(p,msg):progress(.49+p*.49,"Experiment: "+msg)
            first_data=run_sumo(baseline,frames=False,progress=first)
            second_data=run_sumo(request,frames=False,progress=second)
            data={"baseline":first_data["metrics"],"experiment":second_data["metrics"],
                  "baseline_warnings":first_data["warnings"],"experiment_warnings":second_data["warnings"],
                  "note":"SUMO-generated net and demand; zero-offset baseline is not the observed Rome controller plan."}
        _update(jid,status="complete",progress=1.,message="Completed",result=data)
    except Exception as exc:
        _update(jid,status="failed",message=str(exc)[:1200],progress=1.)
