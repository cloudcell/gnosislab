"""Profile conformance for gnosislab crates.

Decision recorded per the e-plan: crates conform to base RO-Crate 1.3.
Adoption of an upstream run profile (Workflow Run RO-Crate /
Provenance Run Crate) is deferred until a spec-comparison pass shows
the mapping satisfies it — ``conformsTo`` is data, so upgrading is a
constant change, not a structure change.
"""

RO_CRATE_1_3 = "https://w3id.org/ro/crate/1.3"

# The single conformance claim made today. A workflow-run profile URI
# lands here when the mapping is verified against it.
CONFORMS_TO = [RO_CRATE_1_3]

# Contextual agent entity: the executor that ran the trials.
GNOSISLAB_AGENT = {
    "@id": "#gnosislab",
    "@type": "SoftwareApplication",
    "name": "gnosislab (ml-episteme)",
    "url": "https://github.com/cloudcell/gnosislab",
    "description": (
        "Scientific Experiment MCP server — hypothesis → experiment → "
        "evidence → conclusion. Trial actions are executed and recorded "
        "by this software."
    ),
}
