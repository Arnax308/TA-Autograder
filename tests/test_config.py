import os
import pytest
from pathlib import Path
from grader.config import AssignmentLoader, AssignmentManager, SystemConfig
from grader.models import LanguageType

def test_load_week1_assignment(tmp_path):
    target = AssignmentManager.create_new_assignment("sample_lab", base_dir=str(tmp_path))
    cfg = AssignmentLoader.load_assignment(target)
    assert cfg.id == "sample_lab"
    assert len(cfg.questions) >= 1

def test_new_assignment_scaffolding(tmp_path):
    target = AssignmentManager.create_new_assignment(
        name="test_assignment",
        title="Test Assignment",
        description="Testing scaffolding",
        base_dir=str(tmp_path)
    )
    assert target.exists()
    cfg = AssignmentLoader.load_assignment(target)
    assert cfg.id == "test_assignment"
    assert len(cfg.questions) == 1

def test_add_question_to_assignment(tmp_path):
    target = AssignmentManager.create_new_assignment(
        name="week2_test",
        base_dir=str(tmp_path)
    )
    new_q = AssignmentManager.add_question_to_assignment(
        assignment_id="week2_test",
        question_id="q2",
        name="Question 2",
        description="Testing add question",
        language="cpp",
        base_dir=str(tmp_path)
    )
    assert new_q.id == "q2"
    assert new_q.language == LanguageType.CPP

    cfg = AssignmentLoader.load_assignment(target)
    assert len(cfg.questions) == 2

def test_assignment_and_question_crud(tmp_path):
    # 1. Create
    target = AssignmentManager.create_new_assignment(
        name="crud_lab",
        title="Original Title",
        description="Original Desc",
        base_dir=str(tmp_path)
    )
    assert target.exists()

    # 2. Update Assignment
    updated_cfg = AssignmentManager.update_assignment(
        assignment_id="crud_lab",
        title="Updated Title",
        description="Updated Desc",
        base_dir=str(tmp_path)
    )
    assert updated_cfg.title == "Updated Title"
    assert updated_cfg.description == "Updated Desc"

    # 3. Add Question
    AssignmentManager.add_question_to_assignment(
        assignment_id="crud_lab",
        question_id="q2",
        name="Question 2",
        description="Q2 desc",
        language="c",
        base_dir=str(tmp_path)
    )
    cfg = AssignmentManager.get_assignment("crud_lab", base_dir=str(tmp_path))
    assert len(cfg.questions) == 2

    # 4. Update Question
    updated_q = AssignmentManager.update_question(
        assignment_id="crud_lab",
        question_id="q2",
        name="Updated Q2 Name",
        timeout_sec=5.0,
        base_dir=str(tmp_path)
    )
    assert updated_q.name == "Updated Q2 Name"
    assert updated_q.timeout_sec == 5.0

    # 5. Delete Question
    deleted_q = AssignmentManager.delete_question("crud_lab", "q1", base_dir=str(tmp_path))
    assert deleted_q is True
    cfg_after_del_q = AssignmentManager.get_assignment("crud_lab", base_dir=str(tmp_path))
    assert len(cfg_after_del_q.questions) == 1
    assert cfg_after_del_q.questions[0].id == "q2"

    # 6. Delete Assignment
    del_assign = AssignmentManager.delete_assignment("crud_lab", base_dir=str(tmp_path))
    assert del_assign is True
    assert not target.exists()

def test_compiler_discovery():
    gcc_path, ctype = SystemConfig.find_compiler(LanguageType.C)
    assert gcc_path is not None
    assert Path(gcc_path).exists()

