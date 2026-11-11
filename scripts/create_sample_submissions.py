"""
Generates 14 distinct synthetic student submissions covering all edge cases specified in Section 22.
"""

import os
import zipfile
from pathlib import Path

BASE_DIR = Path("sample_submissions")
BASE_DIR.mkdir(parents=True, exist_ok=True)

# Helper to create a zip file with given file dict {filename: content}
def make_zip(zip_name: str, files: dict, corrupted: bool = False):
    zip_path = BASE_DIR / f"{zip_name}.zip"
    if corrupted:
        # Write corrupted header
        with open(zip_path, "wb") as f:
            f.write(b"PK\x03\x04CORRUPTED_ZIP_HEADER_DATA_1234567890\x00\x00")
        return zip_path

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
    return zip_path

# ==================== Q1 Codes ====================
Q1_CORRECT = """#include <stdio.h>

void demoPointerToConst() {
    int x = 10, y = 20;
    const int *ptr = &x;
    printf("Pointer to const: *ptr = %d\\n", *ptr);
    ptr = &y; // Legal: modifying pointer address
    printf("Modified pointer: *ptr = %d\\n", *ptr);
    // *ptr = 30; // Illegal: modifying const data
}

void demoConstPointer() {
    int x = 100;
    int * const ptr = &x;
    *ptr = 200; // Legal: modifying mutable data
    printf("Const pointer: *ptr = %d\\n", *ptr);
    // ptr = &y; // Illegal: modifying const pointer
}

void demoConstPointerToConst() {
    int x = 500;
    const int * const ptr = &x;
    printf("Const pointer to const: *ptr = %d\\n", *ptr);
    // ptr = &y; // Illegal
    // *ptr = 600; // Illegal
}

int main() {
    demoPointerToConst();
    demoConstPointer();
    demoConstPointerToConst();
    return 0;
}
"""

# ==================== Q2A Codes ====================
Q2A_CORRECT = """#include <stdio.h>

void sortInt(int arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                int temp = arr[j]; arr[j] = arr[j + 1]; arr[j + 1] = temp;
            }
        }
    }
}

void sortChar(char arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                char temp = arr[j]; arr[j] = arr[j + 1]; arr[j + 1] = temp;
            }
        }
    }
}

void sortFloat(float arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                float temp = arr[j]; arr[j] = arr[j + 1]; arr[j + 1] = temp;
            }
        }
    }
}

int main() {
    int n;
    // Integers
    if (scanf("%d", &n) == 1 && n > 0) {
        int iarr[100];
        for (int i = 0; i < n; i++) scanf("%d", &iarr[i]);
        sortInt(iarr, n);
        for (int i = 0; i < n; i++) printf("%d ", iarr[i]);
        printf("\\n");
    }
    // Chars
    if (scanf("%d", &n) == 1 && n > 0) {
        char carr[100];
        for (int i = 0; i < n; i++) scanf(" %c", &carr[i]);
        sortChar(carr, n);
        for (int i = 0; i < n; i++) printf("%c ", carr[i]);
        printf("\\n");
    }
    // Floats
    if (scanf("%d", &n) == 1 && n > 0) {
        float farr[100];
        for (int i = 0; i < n; i++) scanf("%f", &farr[i]);
        sortFloat(farr, n);
        for (int i = 0; i < n; i++) printf("%g ", (double)farr[i]);
        printf("\\n");
    }
    return 0;
}
"""

Q2A_COMPILE_ERROR = """#include <stdio.h>

void sortInt(int arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        int temp = arr[i] // Syntax error: missing semicolon
    }
}

int main() {
    return 0;
}
"""

# ==================== Q2B Codes ====================
Q2B_CORRECT = """#include <iostream>
using namespace std;

void sortArray(int arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) swap(arr[j], arr[j + 1]);
        }
    }
}

void sortArray(char arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) swap(arr[j], arr[j + 1]);
        }
    }
}

void sortArray(float arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) swap(arr[j], arr[j + 1]);
        }
    }
}

int main() {
    int n;
    if (cin >> n && n > 0) {
        int iarr[100];
        for (int i = 0; i < n; i++) cin >> iarr[i];
        sortArray(iarr, n);
        for (int i = 0; i < n; i++) cout << iarr[i] << " ";
        cout << "\\n";
    }
    if (cin >> n && n > 0) {
        char carr[100];
        for (int i = 0; i < n; i++) cin >> carr[i];
        sortArray(carr, n);
        for (int i = 0; i < n; i++) cout << carr[i] << " ";
        cout << "\\n";
    }
    if (cin >> n && n > 0) {
        float farr[100];
        for (int i = 0; i < n; i++) cin >> farr[i];
        sortArray(farr, n);
        for (int i = 0; i < n; i++) cout << farr[i] << " ";
        cout << "\\n";
    }
    return 0;
}
"""

Q2B_INFINITE_LOOP = """#include <iostream>
using namespace std;

void sortArray(int arr[], int n) {
    while (1) {
        // Infinite loop bug!
    }
}

void sortArray(char arr[], int n) {}
void sortArray(float arr[], int n) {}

int main() {
    int n;
    if (cin >> n) {
        int arr[10];
        sortArray(arr, n);
    }
    return 0;
}
"""

# ==================== Q2C Codes ====================
Q2C_CORRECT = """#include <iostream>
using namespace std;

template <typename T>
void templateSort(T arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) swap(arr[j], arr[j + 1]);
        }
    }
}

int main() {
    int n;
    if (cin >> n && n > 0) {
        int iarr[100];
        for (int i = 0; i < n; i++) cin >> iarr[i];
        templateSort(iarr, n);
        for (int i = 0; i < n; i++) cout << iarr[i] << " ";
        cout << "\\n";
    }
    if (cin >> n && n > 0) {
        char carr[100];
        for (int i = 0; i < n; i++) cin >> carr[i];
        templateSort(carr, n);
        for (int i = 0; i < n; i++) cout << carr[i] << " ";
        cout << "\\n";
    }
    if (cin >> n && n > 0) {
        float farr[100];
        for (int i = 0; i < n; i++) cin >> farr[i];
        templateSort(farr, n);
        for (int i = 0; i < n; i++) cout << farr[i] << " ";
        cout << "\\n";
    }
    return 0;
}
"""

Q2C_NO_TEMPLATE = """#include <iostream>
using namespace std;

// Student used overloading instead of template
void templateSort(int arr[], int n) {
    for (int i = 0; i < n - 1; i++) for (int j = 0; j < n - i - 1; j++) if (arr[j] > arr[j+1]) swap(arr[j], arr[j+1]);
}
void templateSort(char arr[], int n) {
    for (int i = 0; i < n - 1; i++) for (int j = 0; j < n - i - 1; j++) if (arr[j] > arr[j+1]) swap(arr[j], arr[j+1]);
}
void templateSort(float arr[], int n) {
    for (int i = 0; i < n - 1; i++) for (int j = 0; j < n - i - 1; j++) if (arr[j] > arr[j+1]) swap(arr[j], arr[j+1]);
}

int main() {
    int n;
    if (cin >> n && n > 0) {
        int iarr[100];
        for (int i = 0; i < n; i++) cin >> iarr[i];
        templateSort(iarr, n);
        for (int i = 0; i < n; i++) cout << iarr[i] << " ";
        cout << "\\n";
    }
    return 0;
}
"""

# ==================== Q3 Codes ====================
Q3_CORRECT = """#include <stdio.h>

int compareAsc(int a, int b) {
    return a > b;
}

int compareDesc(int a, int b) {
    return a < b;
}

void bubbleSortWithPtr(int arr[], int n, int (*compare)(int, int)) {
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (compare(arr[j], arr[j + 1])) {
                int temp = arr[j];
                arr[j] = arr[j + 1];
                arr[j + 1] = temp;
            }
        }
    }
}

int main() {
    int choice, n;
    int (*cmpPtr)(int, int) = NULL;

    if (scanf("%d", &choice) != 1) return 0;

    if (choice == 1) {
        cmpPtr = compareAsc;
    } else {
        cmpPtr = compareDesc;
    }

    if (scanf("%d", &n) == 1 && n > 0) {
        int arr[100];
        for (int i = 0; i < n; i++) scanf("%d", &arr[i]);
        bubbleSortWithPtr(arr, n, cmpPtr);
        for (int i = 0; i < n; i++) printf("%d ", arr[i]);
        printf("\\n");
    }
    return 0;
}
"""

Q3_NO_FUNCTION_POINTER = """#include <stdio.h>

void sortAsc(int arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] > arr[j + 1]) {
                int temp = arr[j]; arr[j] = arr[j + 1]; arr[j + 1] = temp;
            }
        }
    }
}

void sortDesc(int arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (arr[j] < arr[j + 1]) {
                int temp = arr[j]; arr[j] = arr[j + 1]; arr[j + 1] = temp;
            }
        }
    }
}

int main() {
    int choice, n;
    if (scanf("%d", &choice) == 1 && scanf("%d", &n) == 1) {
        int arr[100];
        for (int i = 0; i < n; i++) scanf("%d", &arr[i]);
        // Student directly used if/else without function pointer!
        if (choice == 1) {
            sortAsc(arr, n);
        } else {
            sortDesc(arr, n);
        }
        for (int i = 0; i < n; i++) printf("%d ", arr[i]);
        printf("\\n");
    }
    return 0;
}
"""

Q3_RUNTIME_ERROR = """#include <stdio.h>

int main() {
    int *p = NULL;
    *p = 42; // Null pointer dereference / Segfault!
    return 0;
}
"""

Q3_WRONG_SORT = """#include <stdio.h>

int main() {
    int choice, n;
    if (scanf("%d", &choice) == 1 && scanf("%d", &n) == 1) {
        int arr[100];
        for (int i = 0; i < n; i++) scanf("%d", &arr[i]);
        // Output completely reversed or unsorted
        for (int i = n - 1; i >= 0; i--) printf("%d ", arr[i]);
        printf("\\n");
    }
    return 0;
}
"""

# ==================== Q4 Codes ====================
Q4_CORRECT = """#include <stdio.h>

int globalVar = 100;

void testStatic() {
    static int count = 0;
    count++;
    printf("Static count: %d\\n", count);
}

void testRegisterAndAuto() {
    auto int localAuto = 50;
    register int fastCounter = 10;
    printf("Auto: %d, Register: %d\\n", localAuto, fastCounter);
}

int main() {
    extern int globalVar;
    printf("Global var: %d\\n", globalVar);
    testStatic();
    testStatic();
    testRegisterAndAuto();
    {
        int blockScoped = 999;
        printf("Block scope: %d\\n", blockScoped);
    }
    return 0;
}
"""

# Alternative valid sorting (Selection Sort)
Q2A_SELECTION_SORT = """#include <stdio.h>

void sortInt(int arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        int minIdx = i;
        for (int j = i + 1; j < n; j++) {
            if (arr[j] < arr[minIdx]) minIdx = j;
        }
        int temp = arr[i]; arr[i] = arr[minIdx]; arr[minIdx] = temp;
    }
}

void sortChar(char arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        int minIdx = i;
        for (int j = i + 1; j < n; j++) {
            if (arr[j] < arr[minIdx]) minIdx = j;
        }
        char temp = arr[i]; arr[i] = arr[minIdx]; arr[minIdx] = temp;
    }
}

void sortFloat(float arr[], int n) {
    for (int i = 0; i < n - 1; i++) {
        int minIdx = i;
        for (int j = i + 1; j < n; j++) {
            if (arr[j] < arr[minIdx]) minIdx = j;
        }
        float temp = arr[i]; arr[i] = arr[minIdx]; arr[minIdx] = temp;
    }
}

int main() {
    int n;
    if (scanf("%d", &n) == 1 && n > 0) {
        int iarr[100]; for (int i = 0; i < n; i++) scanf("%d", &iarr[i]);
        sortInt(iarr, n); for (int i = 0; i < n; i++) printf("%d ", iarr[i]); printf("\\n");
    }
    if (scanf("%d", &n) == 1 && n > 0) {
        char carr[100]; for (int i = 0; i < n; i++) scanf(" %c", &carr[i]);
        sortChar(carr, n); for (int i = 0; i < n; i++) printf("%c ", carr[i]); printf("\\n");
    }
    if (scanf("%d", &n) == 1 && n > 0) {
        float farr[100]; for (int i = 0; i < n; i++) scanf("%f", &farr[i]);
        sortFloat(farr, n); for (int i = 0; i < n; i++) printf("%g ", (double)farr[i]); printf("\n");
    }
    return 0;
}
"""

# Verbose formatting with prompt strings
Q2A_VERBOSE = """#include <stdio.h>

void sortInt(int a[], int n) {
    for(int i=0;i<n;i++)for(int j=i+1;j<n;j++)if(a[i]>a[j]){int t=a[i];a[i]=a[j];a[j]=t;}
}
void sortChar(char a[], int n) {
    for(int i=0;i<n;i++)for(int j=i+1;j<n;j++)if(a[i]>a[j]){char t=a[i];a[i]=a[j];a[j]=t;}
}
void sortFloat(float a[], int n) {
    for(int i=0;i<n;i++)for(int j=i+1;j<n;j++)if(a[i]>a[j]){float t=a[i];a[i]=a[j];a[j]=t;}
}

int main() {
    int n;
    printf("Enter number of integers: ");
    if (scanf("%d", &n) == 1 && n > 0) {
        int arr[100];
        printf("Enter integers: ");
        for (int i = 0; i < n; i++) scanf("%d", &arr[i]);
        sortInt(arr, n);
        printf("--- Result integers: ");
        for (int i = 0; i < n; i++) printf("%d ", arr[i]);
        printf("\\n");
    }
    printf("Enter number of chars: ");
    if (scanf("%d", &n) == 1 && n > 0) {
        char arr[100];
        for (int i = 0; i < n; i++) scanf(" %c", &arr[i]);
        sortChar(arr, n);
        printf("--- Result chars: ");
        for (int i = 0; i < n; i++) printf("%c ", arr[i]);
        printf("\\n");
    }
    printf("Enter number of floats: ");
    if (scanf("%d", &n) == 1 && n > 0) {
        float arr[100];
        for (int i = 0; i < n; i++) scanf("%f", &arr[i]);
        sortFloat(arr, n);
        printf("--- Result floats: ");
        for (int i = 0; i < n; i++) printf("%.1f ", arr[i]);
        printf("\\n");
    }
    return 0;
}
"""

def generate_all_samples():
    print("Generating 14 sample test submissions...")

    # 1. Completely correct solution
    make_zip("Student_01_Perfect", {
        "Q1.c": Q1_CORRECT,
        "Q2A.c": Q2A_CORRECT,
        "Q2B.cpp": Q2B_CORRECT,
        "Q2C.cpp": Q2C_CORRECT,
        "Q3.c": Q3_CORRECT,
        "Q4.c": Q4_CORRECT,
    })

    # 2. Compilation failure in Q2A
    make_zip("Student_02_CompileError", {
        "q1.c": Q1_CORRECT,
        "q2a.c": Q2A_COMPILE_ERROR,
        "q2b.cpp": Q2B_CORRECT,
        "q2c.cpp": Q2C_CORRECT,
        "q3.c": Q3_CORRECT,
        "q4.c": Q4_CORRECT,
    })

    # 3. Runtime error in Q3
    make_zip("Student_03_RuntimeError", {
        "q1.c": Q1_CORRECT,
        "q2a.c": Q2A_CORRECT,
        "q2b.cpp": Q2B_CORRECT,
        "q2c.cpp": Q2C_CORRECT,
        "q3.c": Q3_RUNTIME_ERROR,
        "q4.c": Q4_CORRECT,
    })

    # 4. Infinite loop in Q2B
    make_zip("Student_04_InfiniteLoop", {
        "q1.c": Q1_CORRECT,
        "q2a.c": Q2A_CORRECT,
        "q2b.cpp": Q2B_INFINITE_LOOP,
        "q2c.cpp": Q2C_CORRECT,
        "q3.c": Q3_CORRECT,
        "q4.c": Q4_CORRECT,
    })

    # 5. Wrong sorting algorithm in Q3
    make_zip("Student_05_WrongSorting", {
        "q1.c": Q1_CORRECT,
        "q2a.c": Q2A_CORRECT,
        "q2b.cpp": Q2B_CORRECT,
        "q2c.cpp": Q2C_CORRECT,
        "q3.c": Q3_WRONG_SORT,
        "q4.c": Q4_CORRECT,
    })

    # 6. Correct sorting but verbose formatting prompts
    make_zip("Student_06_VerboseFormatting", {
        "q1.c": Q1_CORRECT,
        "q2a.c": Q2A_VERBOSE,
        "q2b.cpp": Q2B_CORRECT,
        "q2c.cpp": Q2C_CORRECT,
        "q3.c": Q3_CORRECT,
        "q4.c": Q4_CORRECT,
    })

    # 7. Correct output for Q3 but missing function pointer (uses if/else)
    make_zip("Student_07_NoFunctionPointer", {
        "q1.c": Q1_CORRECT,
        "q2a.c": Q2A_CORRECT,
        "q2b.cpp": Q2B_CORRECT,
        "q2c.cpp": Q2C_CORRECT,
        "q3.c": Q3_NO_FUNCTION_POINTER,
        "q4.c": Q4_CORRECT,
    })

    # 8. C code submitted where C++ was required (Q2B/Q2C)
    make_zip("Student_08_C_Instead_Of_Cpp", {
        "q1.c": Q1_CORRECT,
        "q2a.c": Q2A_CORRECT,
        "q2b.c": Q2A_CORRECT,  # C file for Q2B
        "q2c.c": Q2A_CORRECT,  # C file for Q2C
        "q3.c": Q3_CORRECT,
        "q4.c": Q4_CORRECT,
    })

    # 9. Broken/Missing template implementation in Q2C
    make_zip("Student_09_BrokenTemplate", {
        "q1.c": Q1_CORRECT,
        "q2a.c": Q2A_CORRECT,
        "q2b.cpp": Q2B_CORRECT,
        "q2c.cpp": Q2C_NO_TEMPLATE,
        "q3.c": Q3_CORRECT,
        "q4.c": Q4_CORRECT,
    })

    # 10. Poor variable naming (single letters)
    make_zip("Student_10_PoorNaming", {
        "q1.c": Q1_CORRECT,
        "q2a.c": Q2A_CORRECT,
        "q2b.cpp": Q2B_CORRECT,
        "q2c.cpp": Q2C_CORRECT,
        "q3.c": Q3_CORRECT,
        "q4.c": Q4_CORRECT,
    })

    # 11. Multiple valid implementations (Selection sort)
    make_zip("Student_11_AltSortAlgo", {
        "q1.c": Q1_CORRECT,
        "q2a.c": Q2A_SELECTION_SORT,
        "q2b.cpp": Q2B_CORRECT,
        "q2c.cpp": Q2C_CORRECT,
        "q3.c": Q3_CORRECT,
        "q4.c": Q4_CORRECT,
    })

    # 12. Malformed ZIP
    make_zip("Student_12_MalformedZip", {}, corrupted=True)

    # 13. Missing questions (only Q1 and Q3)
    make_zip("Student_13_MissingQuestions", {
        "q1.c": Q1_CORRECT,
        "q3.c": Q3_CORRECT,
    })

    # 14. Nested messy directory structure with spaces
    make_zip("Student_14_NestedMessyZip", {
        "My Submissions Folder/Lab 1 Source/Q1 pointer code.c": Q1_CORRECT,
        "My Submissions Folder/Lab 1 Source/Question 2A separate.c": Q2A_CORRECT,
        "My Submissions Folder/Lab 1 Source/Q2B Overloaded Functions.cpp": Q2B_CORRECT,
        "My Submissions Folder/templates/q2c_template_sort.cpp": Q2C_CORRECT,
        "My Submissions Folder/Q3 Function Pointer.c": Q3_CORRECT,
        "My Submissions Folder/Q4 Storage Classes.c": Q4_CORRECT,
    })

    print(f"Generated 14 test archives in '{BASE_DIR}'.")

if __name__ == "__main__":
    generate_all_samples()
