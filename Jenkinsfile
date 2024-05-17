#!/usr/bin/env groovy

// Ne garder que 5 builds et 5 artefacts
properties([buildDiscarder(logRotator(artifactNumToKeepStr: '5', numToKeepStr: '5'))])

node {
	 
	stage('checkout'){
		checkout scm
	}
	
	// Ajouter un système de tags
    stage('build docker') {
		def dockerTag = env.BRANCH_NAME.replaceAll('/', '-')
        echo "Docker tag :  intoo/imgpush:${dockerTag}"

        docker.withRegistry( '', "dockerhub" ) {
			def customImage = docker.build("intoo/imgpush:${dockerTag}", '.')
			customImage.push()
			if(env.BRANCH_NAME == 'master'){
				customImage.push('latest')
			}
		}
    }
}
