import pytest
from pathlib import Path
from grader.extractor import Extractor
from grader.config import AssignmentLoader

import zipfile

def test_extract_perfect_submission(tmp_path):
    zip_path = tmp_path / "student1.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("q1.c", 'int main(){ return 0; }')
        zf.writestr("q2.cpp", 'int main(){ return 0; }')

    extracted, err = Extractor.extract_zip(zip_path, target_dir=tmp_path / "student1")
    assert err is None
    assert extracted is not None

    files = Extractor.collect_source_files(extracted)
    assert len(files) == 2

def test_extract_nested_messy_submission(tmp_path):
    zip_path = tmp_path / "student14.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("MyFiles/Sub1/Deep/q1_code.c", 'int main(){ return 0; }')

    extracted, err = Extractor.extract_zip(zip_path, target_dir=tmp_path / "student14")
    assert err is None

    files = Extractor.collect_source_files(extracted)
    assert len(files) == 1

def test_extract_corrupted_zip(tmp_path):
    zip_path = tmp_path / "corrupt.zip"
    zip_path.write_bytes(b"NOT_A_ZIP_FILE_RANDOM_BYTES_12345")
    extracted, err = Extractor.extract_zip(zip_path, target_dir=tmp_path / "corrupted")
    assert err is not None
    assert extracted is None

def test_extract_deeply_nested_roll_number_and_semantic_names(tmp_path):
    import zipfile
    from grader.models import QuestionConfig, AssignmentConfig, LanguageType

    # Create a synthetic zip with arbitrary deep nesting and roll number:
    # 220101099.zip -> 220101099/My_Lab1_Work/Final_Code/Programs/
    zip_file = tmp_path / "220101099.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr(
            "220101099/My_Lab1_Work/Final_Code/Programs/fibonacci_code.c",
            '#include <stdio.h>\nint fib(int n){ if(n<=1) return n; return fib(n-1)+fib(n-2); }\nint main(){ printf("%d\\n", fib(5)); return 0; }'
        )
        zf.writestr(
            "220101099/My_Lab1_Work/Final_Code/Programs/factorial_calc.cpp",
            '#include <iostream>\nlong long fact(int n){ return (n<=1)? 1 : n*fact(n-1); }\nint main(){ std::cout << fact(5) << std::endl; return 0; }'
        )

    extracted, err = Extractor.extract_zip(zip_file, target_dir=tmp_path / "extracted_roll")
    assert err is None
    assert extracted is not None

    files = Extractor.collect_source_files(extracted)
    assert len(files) == 2

    # Assignment with semantic question names
    assignment = AssignmentConfig(
        id="math_lab",
        title="Math Lab: Recursion",
        questions=[
            QuestionConfig(id="q1", name="Fibonacci Sequence", description="Generate Fibonacci numbers", language=LanguageType.C),
            QuestionConfig(id="q2", name="Factorial Calculation", description="Compute factorial of N", language=LanguageType.CPP),
        ]
    )

    mapping = Extractor.map_files_to_questions(files, assignment, use_llm_mapper=False)
    assert len(mapping["q1"]) == 1
    assert mapping["q1"][0].name == "fibonacci_code.c"
    assert len(mapping["q2"]) == 1
    assert mapping["q2"][0].name == "factorial_calc.cpp"

