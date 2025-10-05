### Собрать контейнер:

```docker build -t geant4-dev .```


### Запустить контейнер

```docker run -it --rm -p 8000:8000 -v $PWD/src:/app/src --name geant4-api geant4-dev```

