configs=420  step=0.05  bootstrap=2000

参考:
  --------------------------------------------------------------------------------------------------------------------------------------------
  生产 dense+0.05kw（锚点）                   —       —    0.7074 0.8685 0.9185 0.8817 0.8731
  single dense_st (minmax-同序)        0.7987  0.4851  0.6407 0.8685 0.9185 0.8524 0.8136
  single sparse (minmax-同序)          0.3332  0.2594  0.1333 0.2222 0.2667 0.2520 0.2382
  single colbert512 (minmax-同序)      0.7617  0.3761  0.6981 0.8741 0.9463 0.8645 0.8533
  single colbertfull (minmax-同序)     0.7652  0.3777  0.6981 0.8741 0.9241 0.8634 0.8489
  single kw (minmax-同序)              0.4864  0.4041  0.1667 0.4111 0.4778 0.3865 0.3462
  rerank 上限（按 R 排序）                  1.0000  1.0000  0.8537 0.9481 0.9722 0.9590 0.9537

按 L2 蒸馏 nDCG@5 排序 top-15:
  subset                               norm    solver    w                          distill5 Spearman r@1    r@3    r@5    nDCG   MRR
  colbertfull+dense_st+kw+sparse       zscore  grid      [0.4, 0.45, 0.1, 0.05]     0.8257 0.4791 0.7759 0.8907 0.9685 0.9143 0.9144
  colbert512+dense_st+kw+sparse        minmax  grid      [0.45, 0.4, 0.1, 0.05]     0.8256 0.4761 0.7981 0.8796 0.9685 0.9221 0.9244
  colbert512+dense_st+kw               zscore  grid      [0.45, 0.5, 0.05]          0.8250 0.4669 0.7981 0.9130 0.9685 0.9282 0.9274
  colbert512+dense_st+kw+sparse        zscore  grid      [0.45, 0.4, 0.1, 0.05]     0.8249 0.4752 0.7981 0.8796 0.9685 0.9221 0.9244
  colbert512+dense_st                  none    grid      [0.65, 0.35]               0.8246 0.4597 0.7537 0.9130 0.9630 0.9100 0.9052
  colbertfull+dense_st                 none    grid      [0.6, 0.4]                 0.8246 0.4642 0.7537 0.9130 0.9630 0.9096 0.9052
  colbertfull+dense_st+kw+sparse       minmax  grid      [0.25, 0.65, 0.05, 0.05]   0.8243 0.4883 0.7704 0.9185 0.9463 0.9126 0.9044
  colbertfull+dense_st+kw              minmax  grid      [0.5, 0.4, 0.1]            0.8243 0.4661 0.7981 0.8796 0.9685 0.9218 0.9256
  colbert512+dense_st+kw               minmax  grid      [0.4, 0.5, 0.1]            0.8242 0.4772 0.7981 0.8963 0.9685 0.9247 0.9256
  colbertfull+dense_st+kw              zscore  grid      [0.2, 0.7, 0.1]            0.8240 0.4897 0.7741 0.8963 0.9463 0.9136 0.9137
  colbert512+dense_st+kw               none    grid      [0.6, 0.35, 0.05]          0.8238 0.4749 0.7759 0.8963 0.9685 0.9165 0.9144
  colbert512+dense_st+sparse           zscore  grid      [0.45, 0.5, 0.05]          0.8231 0.4666 0.7648 0.9130 0.9407 0.9094 0.9007
  colbert512+dense_st                  minmax  grid      [0.45, 0.55]               0.8228 0.4626 0.7537 0.9130 0.9630 0.9111 0.9089
  colbert512+dense_st                  zscore  grid      [0.45, 0.55]               0.8224 0.4625 0.7537 0.9130 0.9630 0.9111 0.9089
  colbertfull+dense_st+sparse          minmax  grid      [0.25, 0.7, 0.05]          0.8223 0.4821 0.7370 0.9130 0.9407 0.9007 0.8854

按 L3 qrels recall@1 排序 top-8（**含泄漏，仅作对照**）:
  subset                               norm    solver    w                          distill5 Spearman r@1    r@3    r@5    nDCG   MRR
  colbert512+dense_st+kw               zscore  grid      [0.45, 0.5, 0.05]          0.8250 0.4669 0.7981 0.9130 0.9685 0.9282 0.9274
  colbertfull+dense_st+kw              minmax  grid      [0.5, 0.4, 0.1]            0.8243 0.4661 0.7981 0.8796 0.9685 0.9218 0.9256
  colbert512+dense_st+kw               minmax  grid      [0.4, 0.5, 0.1]            0.8242 0.4772 0.7981 0.8963 0.9685 0.9247 0.9256
  colbert512+dense_st+kw+sparse        minmax  grid      [0.45, 0.4, 0.1, 0.05]     0.8256 0.4761 0.7981 0.8796 0.9685 0.9221 0.9244
  colbert512+dense_st+kw+sparse        zscore  grid      [0.45, 0.4, 0.1, 0.05]     0.8249 0.4752 0.7981 0.8796 0.9685 0.9221 0.9244
  colbertfull+dense_st+kw+sparse       zscore  grid      [0.4, 0.45, 0.1, 0.05]     0.8257 0.4791 0.7759 0.8907 0.9685 0.9143 0.9144
  colbert512+dense_st+kw               none    grid      [0.6, 0.35, 0.05]          0.8238 0.4749 0.7759 0.8963 0.9685 0.9165 0.9144
  colbertfull+dense_st+kw              zscore  grid      [0.2, 0.7, 0.1]            0.8240 0.4897 0.7741 0.8963 0.9463 0.9136 0.9137

选定（L2 最优）: ['colbertfull', 'dense_st', 'kw', 'sparse'] / zscore / grid  w=[0.4, 0.45, 0.1, 0.05]
  L3: r@1 0.7759  r@3 0.8907  r@5 0.9685  nDCG@10 0.9143  MRR 0.9144
  生产锚点: r@1 0.7074  MRR 0.8731
  paired bootstrap Δ(r@1) 95% CI = [+0.0588, +0.2353]  (显著净胜)
  per-query oracle headroom（蒸馏 nDCG@5，2 折）= -0.0866
