"""File formats of SarcoStat annotations and blinded study sessions.

Modules
-------
annotations
    Annotation files, format v1: validator, canonical JSON writer and
    reader (:mod:`sarcostat.io.annotations`).
study
    Session manifests and the dihedral display transforms of blinded
    study sessions (:mod:`sarcostat.io.study`).
provenance
    Digest of the installed source and provenance blocks for exports
    (:mod:`sarcostat.io.provenance`).

Nothing is imported here, so importing :mod:`sarcostat.io` stays cheap;
import the submodules directly.
"""
