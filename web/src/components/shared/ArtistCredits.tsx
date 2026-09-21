import { Fragment } from "react";
import { Link } from "react-router";

import type { ArtistCreditSchema } from "#/api/generated/model";

interface ArtistCreditsProps {
  artists: ArtistCreditSchema[];
}

/**
 * Credited artists as prose, each resolved credit linking to its artist page.
 *
 * A credit without an ``artist_id`` has not resolved to a canonical artist
 * yet, so it renders as plain text rather than a link to nothing.
 */
export function ArtistCredits({ artists }: ArtistCreditsProps) {
  if (artists.length === 0) return null;

  return (
    <>
      {artists.map((artist, index) => (
        <Fragment key={artist.artist_id ?? artist.name}>
          {index > 0 && ", "}
          {artist.artist_id ? (
            <Link
              to={`/artists/${artist.artist_id}`}
              className="transition-colors hover:text-primary"
            >
              {artist.name}
            </Link>
          ) : (
            artist.name
          )}
        </Fragment>
      ))}
    </>
  );
}
