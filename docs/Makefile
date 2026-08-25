# Define variables for Sphinx
SPHINXAPIDOC = sphinx-apidoc
SPHINXBUILD = sphinx-build
SOURCEDIR = source
BUILDDIR = build

# Target to run sphinx-apidoc to generate .rst files
apidoc:
	$(SPHINXAPIDOC) -o $(SOURCEDIR)/api ../ripple1d/

# Target to run sphinx-build to generate HTML docs
html: apidoc
	$(SPHINXBUILD) -b html $(SOURCEDIR) $(BUILDDIR)

# Clean up the generated files
clean:
	rm -rf $(BUILDDIR)/*
	rm -rf $(SOURCEDIR)/api
