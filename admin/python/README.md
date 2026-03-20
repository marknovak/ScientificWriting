# Create combined table of contents

- Update `tex2include.md` with the list of topics you want to include in the Table of Contents
- Run the following Python code

```
python3 combine_toc.py --classes-dir ../../classes -f tex2include.md -o ../../course_info/course_ToC/course_ToC.tex
```

- Compile the resulting `combined_toc.tex` file.