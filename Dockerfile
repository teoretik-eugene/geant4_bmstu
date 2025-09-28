FROM ubuntu:22.04 as builder

# Disable Prompt During Packages Installation
ARG DEBIAN_FRONTEND=noninteractive

# Update Ubuntu Software repository and install packages required to build GEANT4
RUN apt-get update -qq --fix-missing \
  && ln -sf /usr/share/zoneinfo/UTC /etc/localtime \
  && apt-get -y install wget\
  && apt-get -y install dpkg-dev cmake g++ gcc make python3 python3-dev git python3-pip \
  && apt-get -y install libx11-dev xlibmesa-glu-dev libglew-dev libxmu-dev

RUN yes | unminimize

ENV G4_VERSION v11.1.1
WORKDIR /app

RUN mkdir -p /app/geant4/build && \
    mkdir -p /app/geant4/install && \
    mkdir -p /app/geant4/data && \
    cd /app && \
    wget https://gitlab.cern.ch/geant4/geant4/-/archive/${G4_VERSION}/geant4-${G4_VERSION}.tar.gz && \
    tar zxf /app/geant4-${G4_VERSION}.tar.gz  && \
    cd /app/geant4/build && \
    cmake -DCMAKE_INSTALL_PREFIX=/app/geant4/install \
          -DGEANT4_INSTALL_DATA=ON \
          -DGEANT4_INSTALL_DATADIR=/app/geant4/data \
          -DGEANT4_BUILD_MULTITHREADED=ON \
          -DGEANT4_INSTALL_EXAMPLES=ON \
          -DGEANT4_USE_SYSTEM_EXPAT=OFF \
          -DBUILD_STATIC_LIBS=OFF \
          -DBUILD_SHARED_LIBS=ON \
          -DGEANT4_USE_OPENGL_X11=ON \
          -DGEANT4_BUILD_TLS_MODEL=global-dynamic \
          -DCMAKE_CXX_STANDARD=17 \
          /app/geant4-${G4_VERSION} && \
    make -j`nproc` && \
    make install


FROM builder

COPY --from=builder /app/geant4/install /app/geant4/install
COPY --from=builder /app/geant4/data /app/geant4/data

# Копирование entry point скрипта
COPY entry-point.sh /app/entry-point.sh
RUN chmod +x /app/entry-point.sh

# Создаем структуру папок для монтирования
RUN mkdir -p /app/src

# install python requirements
COPY requirements.txt  /app/requirements.txt 
RUN python3 -m pip install -r /app/requirements.txt

# Set environment variables
ENV GEANT4_DIR=/app/geant4/install
ENV GEANT4_DATA=/app/geant4/data
ENV LD_LIBRARY_PATH=${GEANT4_DIR}/lib:${LD_LIBRARY_PATH}
ENV PATH=${GEANT4_DIR}/bin:${PATH}
ENV PYTHONPATH=/app/src

# Установка рабочей директории
WORKDIR /app/src

EXPOSE 8000

ENTRYPOINT ["/app/entry-point.sh"]
CMD ["/bin/bash"]

