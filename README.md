[OK] Wrote 987 rows to /home/csecuser/jobs/792ef8641f4d46a595358e970d12cc99/licenses.xlsx
13:48:50 [INFO] task done: License Collection
13:48:50 [INFO] task start: ASM Audit
Traceback (most recent call last):
  File "/home/csecuser/oss_checks/scanner.py", line 1324, in <module>
    raise SystemExit(main())
                     ~~~~^^
  File "/home/csecuser/oss_checks/scanner.py", line 1315, in main
    return pipeline_flow(
        root=root,
    ...<2 lines>...
        dt_config=dt_config,
    )
  File "/home/csecuser/oss_checks/scanner.py", line 113, in wrapper
    result = fn(*args, **kwargs)
  File "/home/csecuser/oss_checks/scanner.py", line 1104, in pipeline_flow
    asm_transitive_future = run_asm_audit_for_target.submit(
        job_dir=paths.job_dir,
    ...<2 lines>...
        wait_for=[transitive_future],
    )
  File "/home/csecuser/oss_checks/scanner.py", line 80, in submit
    value = self.fn(*args, **kwargs)
  File "/home/csecuser/oss_checks/scanner.py", line 690, in run_asm_audit_for_target
    found = run_asm_audit(scan_root=scan_root, out_file=out_file)
  File "/home/csecuser/oss_checks/asm_core/api.py", line 33, in run_asm_audit
    return write_excel_report(
        root=scan_root.resolve(),
    ...<5 lines>...
        exclude_dirs=normalize_exclude_dirs(opts.exclude_dirs),
    )
  File "/home/csecuser/oss_checks/asm_core/report.py", line 139, in write_excel_report
    rows_inline, rows_other = audit_sources.collect_inline_dfs(
                              ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^
        root,
        ^^^^^
    ...<2 lines>...
        exclude_dirs=exclude_dirs,
        ^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "/home/csecuser/oss_checks/asm_core/audit_sources.py", line 217, in collect_inline_dfs
    category, result = analyze_grep_hit(
                       ~~~~~~~~~~~~~~~~^
        root,
        ^^^^^
    ...<7 lines>...
        cache=cache,
        ^^^^^^^^^^^^
    )
    ^
  File "/home/csecuser/oss_checks/asm_core/audit_sources.py", line 115, in analyze_grep_hit
    if not file_path.is_file():
           ~~~~~~~~~~~~~~~~~^^
  File "/opt/conda/lib/python3.13/pathlib/_abc.py", line 482, in is_file
    return S_ISREG(self.stat(follow_symlinks=follow_symlinks).st_mode)
                   ~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/conda/lib/python3.13/pathlib/_local.py", line 515, in stat
    return os.stat(self, follow_symlinks=follow_symlinks)
           ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
OSError: [Errno 36] File name too long: '/home/csecuser/jobs/792ef8641f4d46a595358e970d12cc99/transitive_libs/","escapeRegExp","escapeChar","bareIdentifier","template","text","settings","oldSettings","offset","render","argument","variable","Error","e","data","fallback","idCounter","uniqueId","prefix","id","chain","instance","_chain","executeBound","sourceFunc","boundFunc","callingContext","partial","boundArgs","placeholder","bound","position","bind","TypeError","callArgs","isArrayLike","flatten","input","depth","strict","output","idx","j","len","bindAll","memoize","hasher","cache","address","delay","wait","setTimeout","defer","throttle","options","timeout","previous","later","leading","throttled","_now","remaining","clearTimeout","trailing","cancel","debounce","immediate","passed","debounced","_args","wrap","wrapper","negate","predicate","compose","start","after","before","memo","once","findKey","createPredicateIndexFinder","dir","array","findIndex","findLastIndex","sortedIndex","low","high","mid","createIndexFinder","predicateFind","item","indexOf","lastIndexOf","find","findWhere","each","createReduce","reducer","initial","reduce","reduceRight","filter","list","reject","every","some","fromIndex","guard","invoke","contextPath","method","pluck","where","computed","lastComputed","v","reStrSymbol","toArray","sample","last","rand","temp","shuffle","sortBy","criteria","left","right","group","behavior","partition","groupBy","indexBy","countBy","pass","size","keyInObj","pick","omit","first","compact","Boolean","_flatten","difference","without","otherArrays","uniq","isSorted","seen","union","arrays","intersection","argsLength","unzip","zip","range","stop","step","ceil","chunk","count","chainResult","mixin","allExports"],"mappings"'
