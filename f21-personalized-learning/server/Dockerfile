FROM maven:3.9.9-eclipse-temurin-17 AS build
WORKDIR /src
COPY server/pom.xml .
COPY server/src src
RUN --mount=type=cache,target=/root/.m2 mvn -B -ntp verify
FROM eclipse-temurin:17-jre@sha256:13cc28a6cc72a38ce1f00c906be3580c1a3e604b8984d694f369a96742abc93b
WORKDIR /app
COPY --from=build /src/target/f21-preflight-0.1.0.jar app.jar
COPY data/manifests/g5-dashboard-artifact-registry.json dashboard/data/manifests/g5-dashboard-artifact-registry.json
COPY artifacts/g5-kt-comparison.json artifacts/g5-04-report-validation.json artifacts/g5-recommendation-comparison.json artifacts/g5-05-report-validation.json artifacts/g5-demo-catalog-validation.json artifacts/g5-demo-catalog-import.json dashboard/artifacts/
EXPOSE 8083
ENTRYPOINT ["java","-jar","app.jar"]
