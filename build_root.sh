export ROOT_BIN=root_v6.28.00.source.tar.gz
wget https://root.cern/download/${ROOT_BIN}\
  && tar -xzvf ${ROOT_BIN}\
  && rm -f ${ROOT_BIN}.source.tar.gz \
  && mkdir root_build root_install && cd root_build \
  && cmake -DCMAKE_CXX_STANDARD=17 -DCMAKE_INSTALL_PREFIX=../root_install ../root-6.28.00 \
  && cmake --build . -- install -j8
