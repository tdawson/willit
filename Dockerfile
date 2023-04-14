# create new
FROM fedora
MAINTAINER Troy Dawson <tdawson@redhat.com>

RUN dnf -y install nginx && \
    dnf clean all
RUN echo "Dockerfile Test on Nginx" > /usr/share/nginx/html/index.html

EXPOSE 8080
CMD ["/usr/sbin/nginx", "-g", "daemon off;"]
