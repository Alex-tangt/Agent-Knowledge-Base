arm                                                  r@1  r@3  r@5  r@10 r@14 r@20   nDCG@10   MRR    miss
A0 dense-only                                        0.619 0.841 0.891 0.959 0.965 0.978   0.8383   0.8178  0
A1 sparse-only                                       0.167 0.378 0.400 0.483 0.483 0.546   0.3298   0.2967  0
A2 colbert-only (128)                                0.602 0.835 0.891 0.926 0.959 0.959   0.7982   0.7732  0
A2 colbert-only (512)                                0.624 0.839 0.930 0.952 0.957 0.970   0.8283   0.7978  0
A2 colbert-only (full)                               0.669 0.839 0.907 0.952 0.957 0.970   0.8476   0.8241  0
A3 OFFICIAL raw .4/.2/.4 (colbertfull)               0.189 0.406 0.439 0.561 0.589 0.730   0.3737   0.3367  0
A4 DEFAULT raw 1:1:1 (colbertfull)                   0.189 0.400 0.406 0.517 0.517 0.552   0.3550   0.3226  0
A5 raw dense.4+sparse.2                              0.189 0.406 0.417 0.517 0.544 0.574   0.3567   0.3268  0
B1 minmax OFFICIAL .4/.2/.4 (full)                   0.641 0.880 0.941 0.952 0.957 0.965   0.8408   0.8300  0
B2 zscore OFFICIAL .4/.2/.4 (full)                   0.663 0.880 0.941 0.952 0.957 0.965   0.8479   0.8374  0
B3 RRF(60) OFFICIAL .4/.2/.4 (full)                  0.524 0.850 0.935 0.957 0.957 0.965   0.7742   0.7334  0
B4 raw dense+colbert .5/.5                           0.685 0.885 0.935 0.965 0.965 0.970   0.8749   0.8623  0
B5 minmax dense+colbert .5/.5                        0.680 0.922 0.941 0.965 0.965 0.970   0.8724   0.8504  0
B6 zscore dense+colbert .5/.5                        0.680 0.922 0.941 0.965 0.965 0.970   0.8751   0.8541  0
B7 minmax dense+sparse .5/.5                         0.267 0.491 0.620 0.730 0.774 0.846   0.5135   0.4698  0
B8 RRF(60) dense+sparse .5/.5                        0.328 0.450 0.581 0.641 0.730 0.859   0.4880   0.4821  0
C0 ST-dense only（锚点，应=0.6407）                        0.641 0.869 0.919 0.974 0.994 1.000   0.8524   0.8136  0
C1 ST-dense + colbertfull raw .5/.5                  0.724 0.896 0.924 0.987 0.987 1.000   0.8988   0.8773  0
C2 ST-dense + colbertfull minmax .5/.5               0.724 0.913 0.946 0.965 0.965 0.993   0.8931   0.8759  0
C3 ST-dense + colbertfull zscore .5/.5               0.746 0.935 0.946 0.965 0.965 0.993   0.9046   0.8937  0
C4 minmax ST-dense.4/sparse.2/colbert.4              0.680 0.869 0.941 0.952 0.957 0.957   0.8527   0.8386  0
C5 minmax ST-dense+sparse .5/.5                      0.289 0.535 0.650 0.752 0.796 0.913   0.5360   0.4899  0
C6 minmax ST-dense+colbert512 .5/.5                  0.746 0.907 0.941 0.965 0.965 0.993   0.9003   0.8852  0

gold 命中位次分布（45 题，题数）：
  A0 dense-only                                        top<=1=32  top<=3=40  top<=5=42  top<=10=44  top<=14=44  top<=20=44
  A1 sparse-only                                       top<=1=8  top<=3=17  top<=5=19  top<=10=24  top<=14=24  top<=20=27
  A2 colbert-only (128)                                top<=1=30  top<=3=39  top<=5=42  top<=10=43  top<=14=44  top<=20=44
  A2 colbert-only (512)                                top<=1=31  top<=3=39  top<=5=44  top<=10=44  top<=14=44  top<=20=44
  A2 colbert-only (full)                               top<=1=33  top<=3=39  top<=5=43  top<=10=44  top<=14=44  top<=20=44
  A3 OFFICIAL raw .4/.2/.4 (colbertfull)               top<=1=9  top<=3=20  top<=5=21  top<=10=27  top<=14=29  top<=20=36
  A4 DEFAULT raw 1:1:1 (colbertfull)                   top<=1=9  top<=3=19  top<=5=20  top<=10=25  top<=14=25  top<=20=28
  A5 raw dense.4+sparse.2                              top<=1=9  top<=3=20  top<=5=20  top<=10=25  top<=14=27  top<=20=29
  B1 minmax OFFICIAL .4/.2/.4 (full)                   top<=1=33  top<=3=42  top<=5=44  top<=10=44  top<=14=44  top<=20=44
  B2 zscore OFFICIAL .4/.2/.4 (full)                   top<=1=34  top<=3=42  top<=5=44  top<=10=44  top<=14=44  top<=20=44
  B3 RRF(60) OFFICIAL .4/.2/.4 (full)                  top<=1=27  top<=3=41  top<=5=44  top<=10=44  top<=14=44  top<=20=44
  B4 raw dense+colbert .5/.5                           top<=1=35  top<=3=42  top<=5=44  top<=10=44  top<=14=44  top<=20=44
  B5 minmax dense+colbert .5/.5                        top<=1=34  top<=3=43  top<=5=44  top<=10=44  top<=14=44  top<=20=44
  B6 zscore dense+colbert .5/.5                        top<=1=34  top<=3=43  top<=5=44  top<=10=44  top<=14=44  top<=20=44
  B7 minmax dense+sparse .5/.5                         top<=1=13  top<=3=25  top<=5=30  top<=10=35  top<=14=37  top<=20=40
  B8 RRF(60) dense+sparse .5/.5                        top<=1=17  top<=3=22  top<=5=30  top<=10=31  top<=14=35  top<=20=40
  C0 ST-dense only（锚点，应=0.6407）                        top<=1=31  top<=3=41  top<=5=43  top<=10=45  top<=14=45  top<=20=45
  C1 ST-dense + colbertfull raw .5/.5                  top<=1=36  top<=3=42  top<=5=43  top<=10=45  top<=14=45  top<=20=45
  C2 ST-dense + colbertfull minmax .5/.5               top<=1=36  top<=3=43  top<=5=44  top<=10=44  top<=14=44  top<=20=45
  C3 ST-dense + colbertfull zscore .5/.5               top<=1=37  top<=3=44  top<=5=44  top<=10=44  top<=14=44  top<=20=45
  C4 minmax ST-dense.4/sparse.2/colbert.4              top<=1=34  top<=3=41  top<=5=44  top<=10=44  top<=14=44  top<=20=44
  C5 minmax ST-dense+sparse .5/.5                      top<=1=14  top<=3=27  top<=5=32  top<=10=36  top<=14=38  top<=20=43
  C6 minmax ST-dense+colbert512 .5/.5                  top<=1=37  top<=3=42  top<=5=44  top<=10=44  top<=14=44  top<=20=45

参照（生产默认：dense + 手写关键词加法，gen-2/池14）：r@1 0.7074  r@3 0.8685  r@5 0.9185  r@10 0.9741  nDCG@10 0.8817  MRR 0.8731  miss 0
