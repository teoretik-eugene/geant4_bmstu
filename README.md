### Собрать контейнер:

```docker build -t geant4-app . ```


### Запустить контейнер

```docker run -it --rm -p 8000:8000 --name geant4-api geant4-app```

