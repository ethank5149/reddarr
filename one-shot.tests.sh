#!/bin/bash

echo "============================== Reddarr - Run Tests =============================="
docker-compose -f docker-compose.test.yml up --build --abort-on-container-exit
echo "================================ Tests Finished ================================="
