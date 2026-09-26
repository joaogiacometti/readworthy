{
  description = "readworthy: a Karakeep webhook that tags each bookmark read, skip or unsure";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      deps = ps: [ ps.httpx ps.fastapi ps.uvicorn ];
    in
    {
      packages = forAll (pkgs: rec {
        default = pkgs.python313Packages.buildPythonApplication {
          pname = "readworthy";
          version = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project.version;
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python313Packages.setuptools ];
          dependencies = deps pkgs.python313Packages;
          nativeCheckInputs = [ pkgs.python313Packages.pytestCheckHook ];
          meta.mainProgram = "readworthy";
        };

        # OCI image of readworthy; built for amd64 and arm64 by .github/workflows/image.yml.
        # Secrets (OPENROUTER_API_KEY, ...) are passed at run time, never baked in.
        # /profile holds the example profile; mount your own profile folder over it.
        image = pkgs.dockerTools.buildLayeredImage {
          name = "readworthy";
          tag = default.version;
          contents = [ default ];
          extraCommands = ''
            mkdir profile
            cp ${./example-profile/readworthy.toml} profile/readworthy.toml
          '';
          config = {
            # readworthy binds 127.0.0.1 by default, unreachable from outside the container.
            Cmd = [ "/bin/readworthy" "--host" "0.0.0.0" ];
            Env = [ "READWORTHY_CONFIG=/profile/readworthy.toml" ];
            User = "65534:65534";
            ExposedPorts."8000/tcp" = { };
            Labels = {
              "org.opencontainers.image.description" = "Karakeep webhook that tags each bookmark read, skip or unsure";
              "org.opencontainers.image.version" = default.version;
              "org.opencontainers.image.source" = "https://github.com/joaogiacometti/readworthy";
            };
          };
        };
      });

      # `nix flake check`: builds the package (which runs pytest) and lints.
      checks = forAll (pkgs: {
        package = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        ruff = pkgs.runCommand "readworthy-ruff" { nativeBuildInputs = [ pkgs.ruff ]; } ''
          cd ${./.}
          ruff check --no-cache .
          ruff format --no-cache --check .
          touch $out
        '';
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python313.withPackages (ps: deps ps ++ [ ps.pytest ]))
            pkgs.ruff
          ];
          # Run from the source tree without installing: `python -m readworthy.api`.
          # Load OPENROUTER_API_KEY etc. from the git-ignored .env, if present.
          shellHook = ''
            export PYTHONPATH="$PWD''${PYTHONPATH:+:$PYTHONPATH}"
            if [ -f .env ]; then set -a; . ./.env; set +a; fi
          '';
        };
      });
    };
}
