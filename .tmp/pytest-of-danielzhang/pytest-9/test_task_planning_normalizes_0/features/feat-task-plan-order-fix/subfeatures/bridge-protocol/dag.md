{
  "tasks": [
    {
      "id": "T-bridge-1",
      "name": "Implement bridge-protocol",
      "description": "bridge-protocol task",
      "file_scope": [],
      "requirement_ids": [
        "REQ-bridge-protocol"
      ],
      "step_ids": [
        "STEP-1"
      ],
      "journey_ids": [],
      "acceptance_criteria": [
        {
          "description": "bridge-protocol acceptance criterion",
          "not_criteria": ""
        }
      ],
      "counterexamples": [],
      "security_concerns": [],
      "testid_assignments": [],
      "verification_gates": [
        "AC-bridge-protocol-1"
      ],
      "reference_material": [
        {
          "source": "Plan STEP-1",
          "content": "bridge-protocol reference material"
        }
      ],
      "subfeature_id": "bridge-protocol",
      "repo_path": "",
      "files": [],
      "dependencies": [],
      "team": 0
    },
    {
      "id": "T-bridge-2",
      "name": "Implement bridge-protocol",
      "description": "bridge-protocol task",
      "file_scope": [],
      "requirement_ids": [
        "REQ-bridge-protocol"
      ],
      "step_ids": [
        "STEP-1"
      ],
      "journey_ids": [],
      "acceptance_criteria": [
        {
          "description": "bridge-protocol acceptance criterion",
          "not_criteria": ""
        }
      ],
      "counterexamples": [],
      "security_concerns": [],
      "testid_assignments": [],
      "verification_gates": [
        "AC-bridge-protocol-1"
      ],
      "reference_material": [
        {
          "source": "Plan STEP-1",
          "content": "bridge-protocol reference material"
        }
      ],
      "subfeature_id": "bridge-protocol",
      "repo_path": "",
      "files": [],
      "dependencies": [
        "T-bridge-1"
      ],
      "team": 0
    }
  ],
  "num_teams": 0,
  "execution_order": [
    [
      "T-bridge-1"
    ],
    [
      "T-bridge-2"
    ]
  ],
  "requirement_coverage": {
    "REQ-bridge": [
      "T-bridge-1",
      "T-bridge-2"
    ]
  },
  "complete": true
}