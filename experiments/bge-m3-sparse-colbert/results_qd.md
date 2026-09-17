arm                                                  r@1  r@3  r@5  r@10 r@14 r@20   nDCG@10   MRR    miss
A0 dense-only                                        0.641 0.869 0.919 0.974 0.994 1.000   0.8524   0.8136  0
A1 sparse-only                                       0.133 0.222 0.267 0.381 0.404 0.470   0.2520   0.2382  0
A2 colbert-only (128)                                0.631 0.846 0.913 0.965 0.970 0.976   0.8389   0.8176  0
A2 colbert-only (512)                                0.698 0.874 0.946 0.965 0.976 0.976   0.8645   0.8533  0
A2 colbert-only (full)                               0.698 0.874 0.924 0.970 0.976 0.989   0.8634   0.8489  0
A3 OFFICIAL raw .4/.2/.4 (colbertfull)               0.544 0.681 0.796 0.870 0.937 0.965   0.7159   0.6763  0
A4 DEFAULT raw 1:1:1 (colbertfull)                   0.433 0.533 0.681 0.793 0.859 0.887   0.6084   0.5670  0
A5 raw dense.4+sparse.2                              0.433 0.567 0.663 0.837 0.848 0.893   0.6267   0.5721  0
B1 minmax OFFICIAL .4/.2/.4 (full)                   0.648 0.841 0.907 0.974 0.989 0.989   0.8447   0.8104  0
B2 zscore OFFICIAL .4/.2/.4 (full)                   0.626 0.841 0.907 0.974 0.989 0.989   0.8333   0.7950  0
B3 RRF(60) OFFICIAL .4/.2/.4 (full)                  0.448 0.796 0.896 0.974 0.989 0.994   0.7360   0.6713  0
B4 raw dense+colbert .5/.5                           0.730 0.919 0.941 0.981 0.981 1.000   0.8981   0.8711  0
B5 minmax dense+colbert .5/.5                        0.754 0.913 0.941 0.976 0.981 1.000   0.9051   0.9007  0
B6 zscore dense+colbert .5/.5                        0.754 0.913 0.941 0.976 0.981 1.000   0.9053   0.9007  0
B7 minmax dense+sparse .5/.5                         0.378 0.437 0.602 0.848 0.893 0.981   0.5735   0.5055  0
B8 RRF(60) dense+sparse .5/.5                        0.289 0.452 0.465 0.648 0.715 0.826   0.4629   0.4330  0
C0 ST-dense only（锚点，应=0.6407）                        0.641 0.869 0.919 0.974 0.994 1.000   0.8524   0.8136  0
C1 ST-dense + colbertfull raw .5/.5                  0.730 0.919 0.941 0.981 0.981 1.000   0.8981   0.8711  0
C2 ST-dense + colbertfull minmax .5/.5               0.754 0.913 0.941 0.976 0.981 1.000   0.9051   0.9007  0
C3 ST-dense + colbertfull zscore .5/.5               0.754 0.913 0.941 0.976 0.981 1.000   0.9053   0.9007  0
C4 minmax ST-dense.4/sparse.2/colbert.4              0.648 0.841 0.907 0.974 0.989 0.989   0.8447   0.8104  0
C5 minmax ST-dense+sparse .5/.5                      0.378 0.437 0.602 0.848 0.893 0.981   0.5735   0.5055  0
C6 minmax ST-dense+colbert512 .5/.5                  0.754 0.913 0.941 0.976 0.981 1.000   0.9093   0.9044  0

gold 命中位次分布（45 题，题数）：
  A0 dense-only                                        top<=1=31  top<=3=41  top<=5=43  top<=10=45  top<=14=45  top<=20=45
  A1 sparse-only                                       top<=1=7  top<=3=11  top<=5=13  top<=10=18  top<=14=19  top<=20=22
  A2 colbert-only (128)                                top<=1=32  top<=3=40  top<=5=43  top<=10=45  top<=14=45  top<=20=45
  A2 colbert-only (512)                                top<=1=35  top<=3=41  top<=5=45  top<=10=45  top<=14=45  top<=20=45
  A2 colbert-only (full)                               top<=1=35  top<=3=41  top<=5=44  top<=10=45  top<=14=45  top<=20=45
  A3 OFFICIAL raw .4/.2/.4 (colbertfull)               top<=1=26  top<=3=32  top<=5=37  top<=10=40  top<=14=43  top<=20=44
  A4 DEFAULT raw 1:1:1 (colbertfull)                   top<=1=21  top<=3=25  top<=5=32  top<=10=36  top<=14=39  top<=20=41
  A5 raw dense.4+sparse.2                              top<=1=21  top<=3=26  top<=5=31  top<=10=38  top<=14=39  top<=20=41
  B1 minmax OFFICIAL .4/.2/.4 (full)                   top<=1=32  top<=3=40  top<=5=43  top<=10=45  top<=14=45  top<=20=45
  B2 zscore OFFICIAL .4/.2/.4 (full)                   top<=1=31  top<=3=40  top<=5=43  top<=10=45  top<=14=45  top<=20=45
  B3 RRF(60) OFFICIAL .4/.2/.4 (full)                  top<=1=23  top<=3=38  top<=5=42  top<=10=45  top<=14=45  top<=20=45
  B4 raw dense+colbert .5/.5                           top<=1=35  top<=3=43  top<=5=44  top<=10=45  top<=14=45  top<=20=45
  B5 minmax dense+colbert .5/.5                        top<=1=38  top<=3=43  top<=5=44  top<=10=45  top<=14=45  top<=20=45
  B6 zscore dense+colbert .5/.5                        top<=1=38  top<=3=43  top<=5=44  top<=10=45  top<=14=45  top<=20=45
  B7 minmax dense+sparse .5/.5                         top<=1=18  top<=3=22  top<=5=29  top<=10=39  top<=14=42  top<=20=45
  B8 RRF(60) dense+sparse .5/.5                        top<=1=14  top<=3=22  top<=5=24  top<=10=31  top<=14=34  top<=20=39
  C0 ST-dense only（锚点，应=0.6407）                        top<=1=31  top<=3=41  top<=5=43  top<=10=45  top<=14=45  top<=20=45
  C1 ST-dense + colbertfull raw .5/.5                  top<=1=35  top<=3=43  top<=5=44  top<=10=45  top<=14=45  top<=20=45
  C2 ST-dense + colbertfull minmax .5/.5               top<=1=38  top<=3=43  top<=5=44  top<=10=45  top<=14=45  top<=20=45
  C3 ST-dense + colbertfull zscore .5/.5               top<=1=38  top<=3=43  top<=5=44  top<=10=45  top<=14=45  top<=20=45
  C4 minmax ST-dense.4/sparse.2/colbert.4              top<=1=32  top<=3=40  top<=5=43  top<=10=45  top<=14=45  top<=20=45
  C5 minmax ST-dense+sparse .5/.5                      top<=1=18  top<=3=22  top<=5=29  top<=10=39  top<=14=42  top<=20=45
  C6 minmax ST-dense+colbert512 .5/.5                  top<=1=38  top<=3=43  top<=5=44  top<=10=45  top<=14=45  top<=20=45

参照（生产默认：dense + 手写关键词加法，gen-2/池14）：r@1 0.7074  r@3 0.8685  r@5 0.9185  r@10 0.9741  nDCG@10 0.8817  MRR 0.8731  miss 0
