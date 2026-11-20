"""UI-agnostic rules of the SarcoStat web app.

The modules of this package hold every interaction rule of the annotation
and study modes, so that the browser view only draws and forwards input:

- :mod:`sarcostat.engine.clock`: time sources of a study session;
- :mod:`sarcostat.engine.study`: the blinded study session;
- :mod:`sarcostat.engine.annotate`: the annotation session and its flows;
- :mod:`sarcostat.engine.document`: annotation format v1 to and from a session;
- :mod:`sarcostat.engine.reports`: the sentences and numbers shown to the user;
- :mod:`sarcostat.engine.bridge`: the JSON command interface used by the web worker.

Nothing here imports Tk, matplotlib, PIL, pandas, typer or rich, and this
package module imports none of its submodules, so that a study session loads
no metric code.
"""
